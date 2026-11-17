"""ADK agents for the earthquake data pipeline.

A SequentialAgent (the orchestrator) runs four specialist LLM agents in order.
Each specialist owns one step and one tool, checks the result, and writes a
short status into shared session state (output_key) for the next agent to read.
"""

import os

from google.adk.agents import Agent, SequentialAgent
from google.adk.models import Gemini
from google.genai import types

from .tools import (
    build_html_report,
    fetch_earthquake_data,
    process_earthquake_data,
    upload_to_gcs,
)

# Model name can be changed in .env (GEMINI_MODEL) without touching code.
# Retries handle "high demand" (503) and rate limit (429) errors automatically.
MODEL = Gemini(
    model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    retry_options=types.HttpRetryOptions(
        attempts=8,
        initial_delay=5,
        max_delay=60,
        http_status_codes=[429, 500, 503],
    ),
)

ingestion_agent = Agent(
    name="ingestion_agent",
    model=MODEL,
    description="Pulls raw earthquake data from the USGS public API.",
    instruction=(
        "You are the data ingestion step of a pipeline. Read the user's request to "
        "find how many days of data and what minimum magnitude they want "
        "(defaults: 7 days, magnitude 2.5). Call fetch_earthquake_data with those "
        "values. If it returns an error, retry once. Then reply with one short "
        "sentence: the number of records pulled and the date range, or the error."
    ),
    tools=[fetch_earthquake_data],
    output_key="ingestion_result",
)

processing_agent = Agent(
    name="processing_agent",
    model=MODEL,
    description="Cleans, filters, groups and aggregates the raw data.",
    instruction=(
        "You are the processing step. Ingestion result: {ingestion_result}\n"
        "If ingestion failed, do not call any tool and reply 'Skipped: ingestion failed.' "
        "Otherwise call process_earthquake_data using the same minimum magnitude the "
        "user asked for (default 2.5). Reply with one short sentence summarizing the "
        "total earthquakes, records dropped, strongest magnitude and most active region."
    ),
    tools=[process_earthquake_data],
    output_key="processing_result",
)

report_agent = Agent(
    name="report_agent",
    model=MODEL,
    description="Builds the HTML visualization page.",
    instruction=(
        "You are the visualization step. Processing result: {processing_result}\n"
        "If processing failed or was skipped, do not call any tool and reply "
        "'Skipped: no processed data.' Otherwise call build_html_report and reply with "
        "one short sentence giving the report file path."
    ),
    tools=[build_html_report],
    output_key="report_result",
)

storage_agent = Agent(
    name="storage_agent",
    model=MODEL,
    description="Uploads raw data, processed data and the report to Google Cloud Storage.",
    instruction=(
        "You are the storage step. Report result: {report_result}\n"
        "If earlier steps failed or were skipped, do not call any tool and reply "
        "'Skipped: nothing to upload.' Otherwise call upload_to_gcs. Then give a short "
        "final pipeline summary for the user: records pulled, key findings, the local "
        "report path, and the list of gs:// paths uploaded (or the upload error)."
    ),
    tools=[upload_to_gcs],
    output_key="storage_result",
)

root_agent = SequentialAgent(
    name="earthquake_pipeline",
    description="Agent-driven pipeline: ingest -> process -> visualize -> store.",
    sub_agents=[ingestion_agent, processing_agent, report_agent, storage_agent],
)
