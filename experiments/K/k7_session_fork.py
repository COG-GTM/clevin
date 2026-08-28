"""K7 — session forking from a checkpoint.

Question: can a Managed Agents session be branched from a checkpoint so two
approaches can be tried from shared history?

Primitive under test: `beta.sessions` surface (SDK reflection) plus direct
probes of plausible REST shapes and of `initial_events`, which is the only
history-seeding input a new session accepts. The driver does not build a fork
mechanism; it establishes whether one exists.

Usage:
  uv run --project runtime python experiments/K/k7_session_fork.py <session_id>
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from common import SMOKE_PREFIX, client, create_session, ids, save


def sdk_surface() -> dict[str, Any]:
    sessions = client().beta.sessions
    methods = sorted(
        name
        for name in dir(sessions)
        if not name.startswith("_")
        and callable(getattr(sessions, name, None))
        and "raw_response" not in name
        and "streaming_response" not in name
    )
    subresources = sorted(
        name
        for name in dir(sessions)
        if not name.startswith("_") and not callable(getattr(sessions, name, None))
    )
    from anthropic.types.beta import session_create_params

    return {
        "sessions_methods": methods,
        "sessions_subresources": subresources,
        "create_params": sorted(
            session_create_params.SessionCreateParams.__annotations__.keys()
        ),
        "initial_event_types": ["user.message", "user.define_outcome"],
    }


def raw(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    api = client()
    url = f"{api.base_url}".rstrip("/") + path
    headers = {
        "x-api-key": api.api_key or "",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "managed-agents-2026-04-01",
        "content-type": "application/json",
    }
    try:
        response = httpx.request(
            method, url, headers=headers, json=body, timeout=60.0
        )
    except Exception as error:  # network-level failure is itself evidence
        return {"path": path, "error": type(error).__name__}
    detail = response.text[:400]
    return {"path": path, "method": method, "status": response.status_code, "body": detail}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: k7_session_fork.py <session_id>")
        return 2
    source = sys.argv[1]
    config = ids()

    probes = [
        raw("POST", f"/v1/sessions/{source}/fork", {}),
        raw("POST", f"/v1/sessions/{source}/branch", {}),
        raw("POST", f"/v1/sessions/{source}/copy", {}),
        raw("POST", f"/v1/sessions/{source}/checkpoints", {}),
        raw("GET", f"/v1/sessions/{source}/checkpoints"),
        raw(
            "POST",
            "/v1/sessions",
            {
                "agent": config["agent_id"],
                "environment_id": config["environment_id"],
                "fork_from_session_id": source,
            },
        ),
        raw(
            "POST",
            "/v1/sessions",
            {
                "agent": config["agent_id"],
                "environment_id": config["environment_id"],
                "source_session_id": source,
            },
        ),
    ]

    # Can a new session be seeded with agent-side history (the minimum a fork
    # would require)?
    seed_history: dict[str, Any]
    try:
        seeded = client().beta.sessions.create(
            agent=config["agent_id"],
            environment_id=config["environment_id"],
            initial_events=[
                {
                    "type": "agent.message",  # type: ignore[typeddict-item]
                    "content": [{"type": "text", "text": "prior turn"}],
                }
            ],
            title="clevin-swarm-K-k7-history-seed",
        )
        seed_history = {"accepted": True, "session_id": seeded.id}
    except Exception as error:
        seed_history = {"accepted": False, "error": str(error)[:400]}

    # Confirm the one thing that *is* possible: a fresh session re-primed with a
    # text digest of prior history (no agent-side state, no sandbox state).
    digest = create_session(
        title="clevin-swarm-K-k7-digest-restart",
        prompt=(
            f"{SMOKE_PREFIX}\nContext digest from an earlier session {source}: the "
            "agent had listed /workspace and found it empty. Confirm you can see "
            "this digest, then state plainly whether you have access to that "
            "earlier session's tool results or its /workspace contents. Do one "
            "harmless `ls -a /workspace` to check, and stop."
        ),
        max_cost="2",
        metadata={"probe": "k7-digest"},
    )

    payload = {
        "source_session_id": source,
        "sdk_surface": sdk_surface(),
        "rest_probes": probes,
        "agent_side_initial_event": seed_history,
        "digest_restart_session_id": digest.id,
    }
    print(save("k7-fork-probes.json", payload))
    for probe in probes:
        print(probe.get("status"), probe["path"], str(probe.get("body"))[:120])
    print("agent-side initial event:", seed_history)
    print("digest restart session:", digest.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
