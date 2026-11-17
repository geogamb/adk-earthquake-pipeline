"""Tools the ADK agents call. Each tool does one pipeline step.

Tools pass data to each other through files in the output/ folder,
so large datasets never have to travel through the LLM.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
RAW_FILE = OUTPUT_DIR / "raw_earthquakes.json"
PROCESSED_FILE = OUTPUT_DIR / "processed_summary.json"
REPORT_FILE = OUTPUT_DIR / "report.html"

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


# ---------------------------------------------------------------- 1. ingest
def fetch_earthquake_data(days: int = 7, min_magnitude: float = 2.5) -> dict:
    """Pulls recent earthquake data from the public USGS API and saves it raw.

    Args:
        days: How many days back to pull data for (1 to 30).
        min_magnitude: Smallest magnitude to include.

    Returns:
        A short status with the number of records and the raw file path.
    """
    days = max(1, min(int(days), 30))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "format": "geojson",
        "starttime": start.strftime("%Y-%m-%d"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_magnitude,
        "orderby": "time",
    }
    try:
        resp = requests.get(USGS_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # report the failure to the agent instead of crashing
        return {"status": "error", "message": f"API request failed: {e}"}

    OUTPUT_DIR.mkdir(exist_ok=True)
    RAW_FILE.write_text(json.dumps(data))
    return {
        "status": "success",
        "records": len(data.get("features", [])),
        "date_range": f"{params['starttime']} to {end.strftime('%Y-%m-%d')}",
        "raw_file": str(RAW_FILE),
    }


# ---------------------------------------------------------------- 2. process
def _region(place: str) -> str:
    """'10 km SW of Ridgecrest, CA' -> 'CA'. Falls back to the full text."""
    if not place:
        return "Unknown"
    region = place.split(",")[-1].strip()
    if " of " in region and "," not in place:
        region = region.split(" of ")[-1].strip()
    if region.lower().startswith("the "):
        region = region[4:]
    return region or "Unknown"


def _bucket(mag: float) -> str:
    if mag < 3:
        return "2.5 to 2.9"
    if mag < 4:
        return "3.0 to 3.9"
    if mag < 5:
        return "4.0 to 4.9"
    if mag < 6:
        return "5.0 to 5.9"
    return "6.0+"


def process_earthquake_data(min_magnitude: float = 2.5) -> dict:
    """Cleans, filters, groups and aggregates the raw earthquake data.

    Steps: drop records with missing magnitude, filter by magnitude,
    remove duplicate IDs, then group by region, by day and by magnitude band.

    Args:
        min_magnitude: Smallest magnitude to keep after cleaning.

    Returns:
        A short status with key summary numbers and the processed file path.
    """
    if not RAW_FILE.exists():
        return {"status": "error", "message": "No raw data found. Run ingestion first."}

    raw = json.loads(RAW_FILE.read_text())
    seen, quakes = set(), []
    dropped = 0
    for f in raw.get("features", []):
        p = f.get("properties", {})
        mag = p.get("mag")
        if f.get("id") in seen or mag is None or mag < min_magnitude:
            dropped += 1
            continue
        seen.add(f.get("id"))
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None, None]
        quakes.append({
            "id": f.get("id"),
            "mag": round(float(mag), 2),
            "place": p.get("place") or "Unknown",
            "region": _region(p.get("place")),
            "time": datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc),
            "depth_km": coords[2],
            "tsunami": bool(p.get("tsunami")),
        })

    if not quakes:
        return {"status": "error", "message": "No earthquakes left after filtering."}

    # group by region
    by_region = defaultdict(list)
    for q in quakes:
        by_region[q["region"]].append(q["mag"])
    regions = sorted(
        ({"region": r, "count": len(m), "avg_mag": round(sum(m) / len(m), 2), "max_mag": max(m)}
         for r, m in by_region.items()),
        key=lambda x: x["count"], reverse=True,
    )

    # group by day
    by_day = defaultdict(list)
    for q in quakes:
        by_day[q["time"].strftime("%Y-%m-%d")].append(q["mag"])
    daily = [{"date": d, "count": len(m), "avg_mag": round(sum(m) / len(m), 2)}
             for d, m in sorted(by_day.items())]

    # group by magnitude band
    order = ["2.5 to 2.9", "3.0 to 3.9", "4.0 to 4.9", "5.0 to 5.9", "6.0+"]
    bands = defaultdict(int)
    for q in quakes:
        bands[_bucket(q["mag"])] += 1
    mag_bands = [{"band": b, "count": bands[b]} for b in order if bands[b]]

    strongest = sorted(quakes, key=lambda q: q["mag"], reverse=True)[:10]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_quakes": len(quakes),
        "records_dropped": dropped,
        "avg_magnitude": round(sum(q["mag"] for q in quakes) / len(quakes), 2),
        "max_magnitude": strongest[0]["mag"],
        "tsunami_alerts": sum(q["tsunami"] for q in quakes),
        "by_region": regions[:15],
        "by_day": daily,
        "by_magnitude_band": mag_bands,
        "strongest": [
            {**q, "time": q["time"].strftime("%Y-%m-%d %H:%M UTC")} for q in strongest
        ],
    }
    PROCESSED_FILE.write_text(json.dumps(summary, indent=2))
    return {
        "status": "success",
        "total_quakes": summary["total_quakes"],
        "records_dropped": dropped,
        "max_magnitude": summary["max_magnitude"],
        "most_active_region": regions[0]["region"],
        "processed_file": str(PROCESSED_FILE),
    }


# ---------------------------------------------------------------- 3. visualize
def build_html_report() -> dict:
    """Builds an HTML web page with charts and a table from the processed data.

    Returns:
        A short status with the path of the HTML report.
    """
    if not PROCESSED_FILE.exists():
        return {"status": "error", "message": "No processed data found. Run processing first."}

    template = (Path(__file__).parent / "report_template.html").read_text()
    data = PROCESSED_FILE.read_text()
    REPORT_FILE.write_text(template.replace("__DATA__", data))
    return {"status": "success", "report_file": str(REPORT_FILE)}


# ---------------------------------------------------------------- 4. store
def upload_to_gcs() -> dict:
    """Uploads the raw data, processed data and HTML report to Google Cloud Storage.

    The bucket name comes from the GCS_BUCKET_NAME environment variable.
    Files go into a folder named with today's run timestamp.

    Returns:
        A short status with the gs:// paths of the uploaded files.
    """
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if not bucket_name:
        return {"status": "error", "message": "GCS_BUCKET_NAME is not set in .env"}

    try:
        from google.cloud import storage

        client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
        bucket = client.bucket(bucket_name)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        uploads = [
            (RAW_FILE, f"raw/{run_id}/raw_earthquakes.json", "application/json"),
            (PROCESSED_FILE, f"processed/{run_id}/processed_summary.json", "application/json"),
            (REPORT_FILE, f"reports/{run_id}/report.html", "text/html"),
            (REPORT_FILE, "reports/latest/report.html", "text/html"),
        ]
        uploaded = []
        for local, remote, ctype in uploads:
            if local.exists():
                bucket.blob(remote).upload_from_filename(str(local), content_type=ctype)
                uploaded.append(f"gs://{bucket_name}/{remote}")
    except Exception as e:
        return {"status": "error", "message": f"Upload failed: {e}"}

    return {"status": "success", "uploaded": uploaded}
