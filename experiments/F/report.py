"""Render the native events captured for a run into readable evidence.

Usage: ``uv run --project runtime python experiments/F/report.py <artifact dir or session json> [--chars N]``

Pure observation of native session/thread events already persisted by ``harness.py``;
prints delegated task text, child replies, tool commands, per-thread stats and stop reasons
so the findings file can quote platform-recorded evidence rather than agent claims.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "thinking":
                parts.append("[thinking] " + str(block.get("thinking", ""))[:400])
            else:
                parts.append(json.dumps(block)[:600])
    return "\n".join(parts)


def render(path: Path, limit: int) -> None:
    data = json.loads(path.read_text())
    print(f"\n########## {path.name}  session={data.get('session', {}).get('id')}")
    threads = {t["id"]: t for t in data.get("threads", []) if isinstance(t, dict) and "id" in t}
    for thread in threads.values():
        print(
            f"  thread {thread['id']} agent={thread.get('agent_name')} "
            f"parent={thread.get('parent_thread_id')} status={thread.get('status')} "
            f"cost={(thread.get('stats') or {}).get('list_cost')}"
        )
    for event in data.get("events", []):
        etype = event.get("type")
        if etype in {"agent.message", "agent.thread_message_sent", "agent.thread_message_received"}:
            who = event.get("session_thread_id")
            extra = event.get("to_agent_name") or event.get("from_agent_name") or ""
            body = text_of(event.get("content"))[:limit]
            print(f"\n--- {etype} thread={who} {extra}\n{body}")
        elif etype == "agent.tool_use":
            body = json.dumps(event.get("input"))[:limit]
            print(f"\n--- tool_use {event.get('name')} thread={event.get('session_thread_id')}\n{body}")
        elif etype == "agent.tool_result":
            body = text_of(event.get("content"))[:limit]
            print(f"\n--- tool_result thread={event.get('session_thread_id')}\n{body}")
        elif etype == "session.usage":
            print(f"\n--- usage {json.dumps(event.get('usage', event))[:limit]}")
        elif etype and etype.startswith(("session.thread_status", "session.status", "agent.thread_context")):
            print(f"--- {etype} thread={event.get('session_thread_id')} stop={json.dumps(event.get('stop_reason'))}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--chars", type=int, default=1200)
    args = parser.parse_args()
    target = Path(args.target)
    files = (
        sorted(p for p in target.glob("sesn_*.json"))
        if target.is_dir()
        else [target]
    )
    for path in files:
        render(path, args.chars)


if __name__ == "__main__":
    main()
