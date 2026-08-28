#!/usr/bin/env python3
"""Probe the delivery semantics of Clevin's native lifecycle webhook handler."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from clevin_runtime import claude_webhook_handler as handler


class FakeRequest:
    headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return b"probe"


async def run_sequence(event_types: list[str]) -> dict[str, object]:
    pending = list(event_types)
    drain_calls: list[str] = []

    def verify(*_: object) -> object:
        event_type = pending.pop(0)
        return SimpleNamespace(
            data=SimpleNamespace(type=event_type, id="session_probe")
        )

    async def drain(environment_id: str) -> list[dict[str, object]]:
        drain_calls.append(environment_id)
        return []

    original_verify = handler._verify_webhook
    original_drain = handler._drain_work
    handler._verify_webhook = verify
    handler._drain_work = drain
    try:
        responses = [await handler.handle_webhook(FakeRequest()) for _ in event_types]
    finally:
        handler._verify_webhook = original_verify
        handler._drain_work = original_drain
    return {
        "event_types": event_types,
        "responses": responses,
        "drain_calls": drain_calls,
    }


async def main_async(output: Path) -> None:
    os.environ["ANTHROPIC_ENVIRONMENT_ID"] = "environment_probe"
    duplicate = await run_sequence(
        [
            "session.status_run_started",
            "session.status_run_started",
        ]
    )
    delayed = await run_sequence(
        [
            "session.updated",
            "session.status_run_started",
        ]
    )
    reordered = await run_sequence(
        [
            "session.status_run_started",
            "session.updated",
        ]
    )
    result = {
        "duplicate": duplicate,
        "delayed": delayed,
        "reordered": reordered,
        "conclusions": {
            "duplicate_run_started_drains_twice": (len(duplicate["drain_calls"]) == 2),
            "non_run_started_events_are_ignored": all(
                response["status"] == "ignored"
                for response in delayed["responses"] + reordered["responses"]
                if response["event_type"] != "session.status_run_started"
            ),
            "ordering_does_not_change_run_started_drain_count": (
                len(delayed["drain_calls"]) == len(reordered["drain_calls"]) == 1
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/A/results/webhook-delivery.json"),
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.output))


if __name__ == "__main__":
    main()
