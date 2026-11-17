"""Runs the ADK earthquake pipeline from the command line.

Usage:
    python main.py
    python main.py --days 14 --min-magnitude 4.0
"""

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "pipeline_agent" / ".env")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from pipeline_agent.agent import root_agent  # noqa: E402

APP_NAME = "earthquake_pipeline"


async def run(days: int, min_magnitude: float) -> None:
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="user")
    prompt = (
        f"Run the earthquake pipeline for the last {days} days "
        f"with a minimum magnitude of {min_magnitude}."
    )
    print(f"\n>>> {prompt}\n")
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    async for event in runner.run_async(
        user_id="user", session_id=session.id, new_message=message
    ):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"[{event.author}] calling tool: {part.function_call.name}({dict(part.function_call.args or {})})")
            elif part.function_response:
                print(f"[{event.author}] tool result: {part.function_response.response}")
            elif part.text:
                print(f"[{event.author}] {part.text.strip()}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ADK earthquake data pipeline")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-magnitude", type=float, default=2.5)
    args = parser.parse_args()
    asyncio.run(run(args.days, args.min_magnitude))
