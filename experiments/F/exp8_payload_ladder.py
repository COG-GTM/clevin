"""F/exp8 — where is the child→parent payload ceiling?

exp7 showed that a child asked for a ~533 KB reply emits an empty turn (``output_tokens: 0``)
three times out of three, and the parent receives only the placeholder
``[child <thread> completed but produced no text output]``. That leaves the actual ceiling
unmeasured. This ladder delegates one child per size (2 KB, 16 KB, 64 KB, 256 KB) in a single
session and measures the delivered payload from the native ``agent.thread_message_received``
event rather than from anything the parent claims.

Primitive under test: child→parent reporting on native session threads (payload fidelity).
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness import BUILTIN_TOOLS, runner

SIZES = [2_000, 16_000, 64_000, 256_000]

CHILD_SYSTEM = """You are a subagent under test. You will be asked for a payload of N characters.

Do exactly this: run `head -c $((N * 3 / 4 + 16)) /dev/urandom | base64 | tr -d '\\n' | head -c N`
with bash (substituting the N you were given), then reply with EXACTLY:
PAYLOAD_START=<the entire output>=PAYLOAD_END
Nothing else — no summary, no truncation, no commentary. If a tool or the platform refuses,
reply with REFUSED= followed by the verbatim refusal text."""

COORDINATOR = """You are the coordinator of a Managed Agents subagent experiment.

Delegate as instructed. Report only what you actually received; never reconstruct, summarise
or invent a payload. Quote any platform notice verbatim."""

TASK = """CLEVIN_SMOKE_TEST — experiment F/exp8 payload ladder. No git, no MCP, local checks only.

In ONE turn, delegate to each of your four roster agents at once. Give each of them exactly the
task text "Produce a payload of N=<size> characters." using these sizes, one per agent, in
roster order: {sizes}.

As each reply arrives, write ONLY the payload body (between PAYLOAD_START= and =PAYLOAD_END) to
/workspace/ladder_<size>.txt and run `wc -c` on it. Do not attempt to repair a payload.

When all four have reported, finish with one fenced ```json block:
{{"per_size": [{{"size": <int>, "received_chars": <int>, "empty": bool,
  "notice": "<verbatim or none>"}}], "notes": "<what the platform did>"}}"""


def payload_len(text: str) -> int:
    match = re.search(r"PAYLOAD_START=(.*?)=PAYLOAD_END", text, re.S)
    return len(match.group(1)) if match else 0


def main() -> None:
    with runner("exp8_payload_ladder") as run:
        children = [
            run.create_agent(f"ladder-{size}", system=CHILD_SYSTEM) for size in SIZES
        ]
        coordinator = run.create_agent(
            "coordinator-ladder",
            system=COORDINATOR,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [c.id for c in children]},
        )
        session = run.create_session(
            agent_id=coordinator.id,
            prompt=TASK.format(sizes=", ".join(str(s) for s in SIZES)),
            label="ladder",
            max_list_cost="200",
        )
        status = run.wait(session.id, timeout_s=3600)
        collected = run.collect(session.id)

        # Measure delivery from the native events, not from the coordinator's own report.
        name_by_thread: dict[str, str] = {}
        for event in collected["events"]:
            if event.get("type") == "session.thread_created":
                name_by_thread[str(event.get("session_thread_id"))] = str(
                    event.get("agent_name")
                )
        delivered: list[dict[str, Any]] = []
        for event in collected["events"]:
            if event.get("type") != "agent.thread_message_received":
                continue
            text = "".join(
                part.get("text", "")
                for part in (event.get("content") or [])
                if isinstance(part, dict)
            )
            thread = str(event.get("from_session_thread_id"))
            delivered.append(
                {
                    "thread": thread,
                    "agent": name_by_thread.get(thread),
                    "received_event_chars": len(text),
                    "payload_chars": payload_len(text),
                    "empty_placeholder": "produced no text output" in text,
                    "head": text[:80],
                }
            )
        per_thread_out = {
            t["id"]: t.get("output_tokens") for t in collected["summary"]["per_thread"]
        }
        digest = {
            "session_id": session.id,
            "status": status,
            "sizes": SIZES,
            "delivered": delivered,
            "thread_output_tokens": per_thread_out,
            "parent_tail": (collected["summary"]["parent_text_tail"] or [None])[-1],
        }
        run.note("digest", digest)
        print(json.dumps({k: v for k, v in digest.items() if k != "parent_tail"}, indent=2))


if __name__ == "__main__":
    main()
