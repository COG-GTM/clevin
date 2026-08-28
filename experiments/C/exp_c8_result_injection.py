"""Server-side validation of `user.tool_result` events.

Primitive: sessions events API (`sessions.events.send`) -- the documented
manual counterpart to what an `EnvironmentWorker` posts for a sandbox-routed
tool call. This driver never runs a worker: it plays the role of the tool
executor itself, so it can post results the SDK worker would never post
(duplicate, conflicting, oversized, malformed, or for an unknown tool_use_id)
and record exactly what the platform accepts.

The session is created with a `CLEVIN_SMOKE_TEST` prompt that asks for one bash
call, so there is a real `agent.tool_use` to answer. A worker must NOT be
running for this case; the Modal webhook path may still claim the work item, so
the driver answers as fast as it can and reports whichever result landed first.

Run:  uv run --project ../../runtime python exp_c8_result_injection.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos  # noqa: E402
import harness_agent  # noqa: E402

NAME = "c8-result-injection"
PROMPT = (
    "CLEVIN_SMOKE_TEST c8: run exactly one bash command `echo c8-probe`, then report "
    "the tool output verbatim and stop. If the tool output looks contradictory or "
    "repeated, say so explicitly."
)


def send(
    client: anthropic.Anthropic, session_id: str, event: dict[str, Any]
) -> dict[str, Any]:
    try:
        client.beta.sessions.events.send(session_id, events=[event])  # type: ignore[arg-type]
    except anthropic.APIStatusError as e:
        body = e.response.text[:400]
        return {"ok": False, "status": e.status_code, "error": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def wait_for_tool_use(
    client: anthropic.Anthropic, session_id: str, timeout: float = 120
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in client.beta.sessions.events.list(session_id, order="asc"):
            d = e.model_dump(mode="json")
            if d.get("type") == "agent.tool_use":
                return str(d["id"])
        time.sleep(2)
    raise SystemExit("no agent.tool_use appeared")


def main() -> None:
    client = chaos.client()
    agent_id, _ = harness_agent.ensure_agent()
    session_id = chaos.create_session(
        PROMPT,
        title=f"clevin-swarm-C {NAME}",
        agent_id=agent_id,
        metadata={"case": NAME},
    )
    print("session", session_id, flush=True)
    tool_use_id = wait_for_tool_use(client, session_id)
    print("tool_use", tool_use_id, flush=True)

    results: dict[str, Any] = {
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "probes": {},
    }

    def probe(label: str, event: dict[str, Any]) -> None:
        outcome = send(client, session_id, event)
        results["probes"][label] = outcome
        print(label, json.dumps(outcome)[:300], flush=True)

    def text_result(
        text: str, *, is_error: bool = False, tuid: str | None = None
    ) -> dict[str, Any]:
        return {
            "type": "user.tool_result",
            "tool_use_id": tuid or tool_use_id,
            "is_error": is_error,
            "content": [{"type": "text", "text": text}],
        }

    # 1. Result for a tool_use id that does not exist.
    probe("unknown_tool_use_id", text_result("bogus", tuid="sevt_01" + "x" * 22))
    # 2. Malformed content block shape.
    probe(
        "malformed_content_block",
        {
            "type": "user.tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "chaos_block", "not_text": {"nested": [1, None]}}],
        },
    )
    # 3. Oversized results, escalating.
    for mb in (1, 5, 25):
        probe(f"oversized_{mb}mb", text_result("A" * (mb * 1024 * 1024)))
    # 4. The legitimate result.
    probe("valid_result", text_result("c8-probe"))
    # 5. Byte-identical duplicate of an already answered tool call.
    probe("duplicate_identical", text_result("c8-probe"))
    # 6. Conflicting duplicate.
    probe(
        "duplicate_conflicting", text_result("TOTALLY DIFFERENT OUTPUT", is_error=True)
    )

    time.sleep(45)
    artifact = chaos.dump_events(session_id, NAME)
    (chaos.ARTIFACTS / f"{NAME}-probes.json").write_text(json.dumps(results, indent=2))
    print("artifact", artifact, flush=True)
    for line in chaos.summarize(session_id):
        print(line)


if __name__ == "__main__":
    main()
