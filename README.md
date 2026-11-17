# ADK Earthquake Data Pipeline

An agent-driven data engineering pipeline built with Google's Agent Development Kit (ADK).
It pulls live earthquake data from the USGS public API, processes it, saves it to Google
Cloud Storage, and builds an HTML report with charts.

## How it works

```
earthquake_pipeline (SequentialAgent, the orchestrator)
 ├── ingestion_agent   -> fetch_earthquake_data()   USGS API -> output/raw_earthquakes.json
 ├── processing_agent  -> process_earthquake_data() clean, filter, group -> output/processed_summary.json
 ├── report_agent      -> build_html_report()       charts + table -> output/report.html
 └── storage_agent     -> upload_to_gcs()           raw, processed and report -> GCS bucket
```

- Each step is its own LLM agent with one tool. It reads the user's request, calls its tool,
  checks the result, and writes a status to shared session state (`output_key`).
- The next agent reads that status in its instructions and skips its step if something failed.
- Data moves between steps through files, so large datasets never go through the LLM.

**Processing logic:** drops records with no magnitude, removes duplicate IDs, filters by
minimum magnitude, then groups by region (count, average and max magnitude), by day, and by
magnitude band, and finds the 10 strongest earthquakes.

**Cloud storage layout:**
```
gs://<bucket>/raw/<run_id>/raw_earthquakes.json
gs://<bucket>/processed/<run_id>/processed_summary.json
gs://<bucket>/reports/<run_id>/report.html
gs://<bucket>/reports/latest/report.html
```

## Setup

**1. Clone and install** (Python 3.10+)
```bash
git clone <this-repo-url>
cd adk-earthquake-pipeline
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. Get a Gemini API key** at https://aistudio.google.com/apikey

**3. Set up Google Cloud Storage**
```bash
gcloud auth application-default login
gcloud storage buckets create gs://<your-unique-bucket-name> --location=us-central1
```
(Or create the bucket in the Cloud Console under Cloud Storage > Buckets.)

**4. Add your settings**
```bash
cp pipeline_agent/.env.example pipeline_agent/.env
```
Then open `pipeline_agent/.env` and fill in your API key, project ID and bucket name.
`.env` is in `.gitignore`, so it is never committed.

## Run

```bash
python main.py                                   # last 7 days, magnitude 2.5+
python main.py --days 14 --min-magnitude 4.0     # custom
```

Or use the ADK web UI to chat with the pipeline:
```bash
adk web
```
Then open http://localhost:8000, pick `pipeline_agent`, and type something like
"Run the pipeline for the last 3 days, magnitude 4 and up."

Open `output/report.html` in a browser to see the results.

## Project files

| File | Purpose |
|---|---|
| `pipeline_agent/agent.py` | ADK agents and the orchestrator |
| `pipeline_agent/tools.py` | The four pipeline steps |
| `pipeline_agent/report_template.html` | HTML page template (Chart.js) |
| `main.py` | Command line runner |
| `pipeline_agent/.env.example` | Settings template (copy to `.env`) |
