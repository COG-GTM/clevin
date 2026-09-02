"""Workstream I: native-only observability collector.

Consumes *only* Managed Agents surfaces -- `sessions.retrieve`,
`sessions.events.list`, `sessions.threads.list`, `sessions.threads.events.list`,
`environments.work.list`, `environments.work.stats` -- plus the Modal control
plane for the self-hosted execution plane (sandbox object + `clevin-sessions`
volume), which is what the workstream brief names as the sandbox-side log
surface.

It derives the correlation chain the brief asks for

    agent version -> session -> thread -> model span -> tool request
      -> EnvironmentWorker work item -> Modal sandbox -> filesystem
      -> tool result -> subagent thread -> compaction -> final result

and the economics roll-up (session vs per-thread `list_cost`, per-span tokens,
prompt-cache fields, budget) from a single session id. Nothing is persisted
outside the JSON artifact it prints: this is a read-only reducer over native
history, not an observability service.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

USD_MINOR = 100.0


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return None


def _cost(amount: Any) -> float | None:
    """`BetaMonetaryAmount` -> dollars. Minor units arrive as a decimal string."""
    if not amount:
        return None
    data = _dump(amount)
    if not isinstance(data, dict):
        return None
    raw = data.get("amount")
    if raw is None:
        return None
    return float(raw) / USD_MINOR


@dataclass
class Chain:
    """Derived correlation view over one session's native history."""

    session: dict[str, Any] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)
    spans: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    compactions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    status_transitions: list[dict[str, Any]] = field(default_factory=list)
    usage_snapshots: list[dict[str, Any]] = field(default_factory=list)
    threads: list[dict[str, Any]] = field(default_factory=list)
    work_items: list[dict[str, Any]] = field(default_factory=list)
    work_stats: dict[str, Any] = field(default_factory=dict)
    sandbox: dict[str, Any] = field(default_factory=dict)
    economics: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def list_events(
    client: anthropic.Anthropic, session_id: str, thread_id: str | None = None
) -> list[dict[str, Any]]:
    if thread_id is None:
        page = client.beta.sessions.events.list(session_id, order="asc")
    else:
        # Thread event listing takes no `order` parameter (session listing does).
        page = client.beta.sessions.threads.events.list(
            thread_id, session_id=session_id
        )
    return [_dump(event) for event in page]


def _reduce_events(events: list[dict[str, Any]], chain: Chain) -> None:
    """Pair spans and tool calls, and collect phase/usage/compaction markers."""
    span_starts: dict[str, dict[str, Any]] = {}
    tool_uses: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()

    for event in events:
        etype = str(event.get("type"))
        counts[etype] += 1
        at = _ts(event.get("processed_at"))

        if etype == "span.model_request_start":
            span_starts[str(event.get("id"))] = event
        elif etype == "span.model_request_end":
            start = span_starts.get(str(event.get("model_request_start_id")))
            start_at = _ts(start.get("processed_at")) if start else None
            usage = event.get("model_usage") or {}
            chain.spans.append(
                {
                    "start_event_id": event.get("model_request_start_id"),
                    "end_event_id": event.get("id"),
                    "started_at": start.get("processed_at") if start else None,
                    "ended_at": event.get("processed_at"),
                    "latency_s": (
                        None if (at is None or start_at is None) else at - start_at
                    ),
                    "is_error": event.get("is_error"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_creation_input_tokens": usage.get(
                        "cache_creation_input_tokens"
                    ),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "speed": usage.get("speed"),
                    "start_event_present": start is not None,
                }
            )
        elif etype in {
            "agent.tool_use",
            "agent.mcp_tool_use",
            "agent.custom_tool_use",
        }:
            tool_uses[str(event.get("id"))] = event
        elif etype in {
            "agent.tool_result",
            "agent.mcp_tool_result",
            "user.tool_result",
            "user.custom_tool_result",
        }:
            use = tool_uses.get(str(event.get("tool_use_id")))
            use_at = _ts(use.get("processed_at")) if use else None
            content = event.get("content") or []
            chain.tools.append(
                {
                    "tool_use_id": event.get("tool_use_id"),
                    "name": use.get("name") if use else None,
                    "kind": use.get("type") if use else None,
                    "result_kind": etype,
                    "requested_at": use.get("processed_at") if use else None,
                    "completed_at": event.get("processed_at"),
                    "latency_s": (
                        None if (at is None or use_at is None) else at - use_at
                    ),
                    "is_error": event.get("is_error"),
                    "result_bytes": len(json.dumps(content)),
                    "session_thread_id": (
                        use.get("session_thread_id") if use else None
                    ),
                    "input_keys": sorted((use.get("input") or {}).keys())
                    if use
                    else None,
                }
            )
        elif etype == "agent.thread_context_compacted":
            chain.compactions.append(
                {"event_id": event.get("id"), "at": event.get("processed_at")}
            )
        elif etype == "session.error":
            chain.errors.append(
                {
                    "event_id": event.get("id"),
                    "at": event.get("processed_at"),
                    "error": event.get("error"),
                }
            )
        elif etype.startswith("session.status_") or etype in {
            "session.thread_created",
            "session.thread_status_running",
            "session.thread_status_idle",
            "session.thread_status_terminated",
            "session.thread_status_rescheduled",
        }:
            chain.status_transitions.append(
                {
                    "type": etype,
                    "at": event.get("processed_at"),
                    "session_thread_id": event.get("session_thread_id"),
                    "agent_name": event.get("agent_name"),
                    # `requires_action` here is the only native marker that an
                    # idle session is waiting on a tool result nobody will send.
                    "stop_reason": event.get("stop_reason"),
                }
            )
        elif etype == "session.usage":
            usage = event.get("usage") or {}
            chain.usage_snapshots.append(
                {
                    "at": event.get("processed_at"),
                    "list_cost_usd": _cost(usage.get("list_cost")),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "cache_creation": usage.get("cache_creation"),
                    "active_seconds": usage.get("active_seconds"),
                    "budget_usd": _cost(
                        (event.get("budget") or {}).get("max_list_cost")
                    )
                    if event.get("budget")
                    else None,
                }
            )

    chain.event_counts = dict(sorted(counts.items()))
    for span in chain.spans:
        if not span["start_event_present"]:
            chain.gaps.append(
                f"model span {span['end_event_id']} has no matching start event"
            )
    unresolved = len(tool_uses) - len(
        {t["tool_use_id"] for t in chain.tools if t["tool_use_id"]}
    )
    if unresolved > 0:
        chain.gaps.append(
            f"{unresolved} tool_use event(s) never received a result event"
        )


def _collect_threads(
    client: anthropic.Anthropic, session_id: str, with_events: bool
) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for thread in client.beta.sessions.threads.list(session_id):
        data = _dump(thread)
        usage = data.get("usage") or {}
        entry: dict[str, Any] = {
            "id": data.get("id"),
            "parent_thread_id": data.get("parent_thread_id"),
            "agent": data.get("agent"),
            "status": data.get("status"),
            "created_at": data.get("created_at"),
            "stats": data.get("stats"),
            "list_cost_usd": _cost(usage.get("list_cost")),
            "usage": usage,
        }
        if with_events:
            events = list_events(client, session_id, str(data.get("id")))
            entry["event_counts"] = dict(
                sorted(Counter(str(e.get("type")) for e in events).items())
            )
            entry["event_count"] = len(events)
        threads.append(entry)
    return threads


def work_client(default: anthropic.Anthropic) -> anthropic.Anthropic:
    """Work-queue reads authenticate with the *environment key*, not the API key.

    `environments.work.list`/`stats` reject the workspace API key with 401, so a
    native-only monitor needs a second credential to see the execution plane.
    """
    key = os.environ.get("ANTHROPIC_ENVIRONMENT_KEY")
    if not key:
        return default
    # `auth_headers` sends only `Authorization` when `auth_token` is set, so the
    # workspace key is not also presented on these calls.
    return anthropic.Anthropic(auth_token=key)


def _collect_work(
    client: anthropic.Anthropic, environment_id: str, session_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = work_client(client)
    items: list[dict[str, Any]] = []
    for work in client.beta.environments.work.list(environment_id):
        data = _dump(work)
        payload = data.get("data") or {}
        if payload.get("session_id") not in {session_id, None}:
            continue
        if payload.get("session_id") is None and session_id not in json.dumps(payload):
            continue
        items.append(
            {
                "work_id": data.get("id"),
                "state": data.get("state"),
                "created_at": data.get("created_at"),
                "acknowledged_at": data.get("acknowledged_at"),
                "started_at": data.get("started_at"),
                "stopped_at": data.get("stopped_at"),
                "stop_requested_at": data.get("stop_requested_at"),
                "latest_heartbeat_at": data.get("latest_heartbeat_at"),
                "metadata": data.get("metadata"),
                "data_keys": sorted(payload.keys()),
            }
        )
    stats = _dump(client.beta.environments.work.stats(environment_id))
    return items, stats if isinstance(stats, dict) else {}


def _collect_sandbox(session_id: str) -> dict[str, Any]:
    """Modal-side execution-plane view: sandbox object and session volume tree."""
    import modal

    out: dict[str, Any] = {"sandboxes": [], "volume_files": [], "errors": []}
    app_id = os.environ.get("CLEVIN_MODAL_APP_ID", "ap-rntdxyiE8GU1aYtc3PwBiN")
    try:
        # The only session<->sandbox join is the sandbox *name*: the Clevin
        # worker names each sandbox after the session id. Modal sandbox tags are
        # empty, and no native Anthropic event carries a sandbox id.
        sandbox = modal.Sandbox.from_name("clevin", name=session_id)
        out["sandbox_by_name"] = {
            "id": sandbox.object_id,
            "poll": sandbox.poll(),
            "joined_on": "sandbox name == session id",
        }
    except Exception as exc:  # noqa: BLE001 - absence is itself evidence
        out["errors"].append(f"sandbox from_name: {type(exc).__name__}: {exc}")
    try:
        for sandbox in modal.Sandbox.list(app_id=app_id):
            out["sandboxes"].append({"id": sandbox.object_id, "poll": sandbox.poll()})
    except Exception as exc:  # noqa: BLE001 - record, do not fail the collection
        out["errors"].append(f"sandbox list: {exc}")
    try:
        volume = modal.Volume.from_name("clevin-sessions", environment_name="clevin")
        volume.hydrate()  # object_id is unavailable before hydration
        out["volume_id"] = volume.object_id
        for entry in volume.listdir(f"/sessions/{session_id}", recursive=True):
            out["volume_files"].append(
                {"path": entry.path, "size": entry.size, "mtime": entry.mtime}
            )
    except Exception as exc:  # noqa: BLE001 - absence of the tree is itself evidence
        out["errors"].append(f"volume listdir: {exc}")
    return out


def _economics(chain: Chain) -> dict[str, Any]:
    session_usage = (chain.session.get("usage") or {}) if chain.session else {}
    session_cost = _cost(session_usage.get("list_cost"))
    thread_costs = {
        str(t["id"]): t["list_cost_usd"]
        for t in chain.threads
        if t["list_cost_usd"] is not None
    }
    thread_sum = sum(thread_costs.values()) if thread_costs else None
    span_in = sum(s["input_tokens"] or 0 for s in chain.spans)
    span_out = sum(s["output_tokens"] or 0 for s in chain.spans)
    span_cache_read = sum(s["cache_read_input_tokens"] or 0 for s in chain.spans)
    span_cache_create = sum(s["cache_creation_input_tokens"] or 0 for s in chain.spans)
    cache_denominator = span_in + span_cache_read + span_cache_create
    return {
        "session_list_cost_usd": session_cost,
        "thread_list_cost_usd": thread_costs,
        "thread_list_cost_sum_usd": thread_sum,
        "session_minus_thread_sum_usd": (
            None
            if (session_cost is None or thread_sum is None)
            else round(session_cost - thread_sum, 6)
        ),
        "session_usage_tokens": {
            "input": session_usage.get("input_tokens"),
            "output": session_usage.get("output_tokens"),
            "cache_read": session_usage.get("cache_read_input_tokens"),
            "cache_creation": session_usage.get("cache_creation"),
        },
        "span_token_sums": {
            "input": span_in,
            "output": span_out,
            "cache_read": span_cache_read,
            "cache_creation": span_cache_create,
            "spans": len(chain.spans),
        },
        "prompt_cache_read_fraction_of_input": (
            None
            if cache_denominator == 0
            else round(span_cache_read / cache_denominator, 4)
        ),
        "active_seconds": session_usage.get("active_seconds"),
        "stats": chain.session.get("stats") if chain.session else None,
        "budget_usd": _cost(
            ((chain.session.get("budget") or {}).get("max_list_cost"))
            if chain.session and chain.session.get("budget")
            else None
        ),
        "final_usage_snapshot_usd": (
            chain.usage_snapshots[-1]["list_cost_usd"]
            if chain.usage_snapshots
            else None
        ),
        "usage_snapshot_count": len(chain.usage_snapshots),
    }


def _staleness(chain: Chain, collected_at: datetime) -> dict[str, Any]:
    """What a native-only monitor can conclude about a possibly stuck session.

    Three native signals, in order of usefulness: an `agent.tool_use` with no
    matching result, a `requires_action` stop reason on the last idle
    transition, and the age of `latest_heartbeat_at` on the self-hosted work
    item. None of them is an alert; all of them must be derived by the reader.
    """
    now = collected_at.timestamp()
    pending = [
        {
            "tool_use_id": tool["tool_use_id"],
            "name": tool["name"],
            "requested_at": tool["requested_at"],
            "age_s": round(now - ts, 3) if (ts := _ts(tool["requested_at"])) else None,
        }
        for tool in chain.tools
        if tool.get("completed_at") is None
    ]
    idle = [t for t in chain.status_transitions if t["type"] == "session.status_idle"]
    last_idle = idle[-1] if idle else None
    heartbeats = [
        {
            "work_id": item["work_id"],
            "state": item["state"],
            "latest_heartbeat_at": item["latest_heartbeat_at"],
            "heartbeat_age_s": round(now - ts, 3)
            if (ts := _ts(item["latest_heartbeat_at"]))
            else None,
        }
        for item in chain.work_items
    ]
    return {
        "pending_tool_uses": pending,
        "last_idle_stop_reason": (last_idle or {}).get("stop_reason"),
        "work_item_heartbeats": heartbeats,
        "native_alert_exists": False,
    }


def _compaction_windows(chain: Chain) -> list[dict[str, Any]]:
    """Everything a compaction event does *not* say, reconstructed from spans.

    `agent.thread_context_compacted` carries only id/type/processed_at, so the
    cost of compacting and the size of the retained context are only inferable:
    the summarisation request appears as an ordinary model span at the same
    timestamp, and post-compaction context size shows up as the next span's
    `cache_creation_input_tokens`.
    """
    windows: list[dict[str, Any]] = []
    for compaction in chain.compactions:
        at = compaction.get("at")
        ts = _ts(at)
        if ts is None:
            continue
        before = [s for s in chain.spans if (b := _ts(s["started_at"])) and b < ts]
        after = [s for s in chain.spans if (a := _ts(s["started_at"])) and a >= ts]
        summarisation = after[0] if after else None
        resumed = after[1] if len(after) > 1 else None
        last_before = before[-1] if before else None
        windows.append(
            {
                "event_id": compaction.get("event_id"),
                "at": at,
                "event_payload_fields": sorted(compaction),
                "span_before": last_before,
                "summarisation_span": summarisation,
                "first_span_after": resumed,
                "context_before_tokens_est": (
                    (last_before or {}).get("cache_read_input_tokens", 0)
                    + (last_before or {}).get("cache_creation_input_tokens", 0)
                    if last_before
                    else None
                ),
                "context_after_tokens_est": (
                    (resumed or {}).get("cache_creation_input_tokens")
                    if resumed
                    else None
                ),
                "summarisation_cost_tokens": (
                    {
                        "input": summarisation.get("input_tokens"),
                        "output": summarisation.get("output_tokens"),
                    }
                    if summarisation
                    else None
                ),
                "tokens_reported_by_event": None,
            }
        )
    return windows


def collect(
    session_id: str,
    *,
    thread_events: bool = True,
    work: bool = True,
    sandbox: bool = True,
    raw_events: bool = False,
) -> dict[str, Any]:
    client = _client()
    chain = Chain()
    session = _dump(client.beta.sessions.retrieve(session_id))
    chain.session = session if isinstance(session, dict) else {}
    events = list_events(client, session_id)
    _reduce_events(events, chain)
    chain.threads = _collect_threads(client, session_id, thread_events)

    environment_id = str(chain.session.get("environment_id") or "")
    if work and environment_id:
        try:
            chain.work_items, chain.work_stats = _collect_work(
                client, environment_id, session_id
            )
        except anthropic.APIStatusError as exc:
            chain.gaps.append(f"work queue not readable with this key: {exc}")
    if sandbox:
        chain.sandbox = _collect_sandbox(session_id)
    chain.economics = _economics(chain)

    agent = chain.session.get("agent") or {}
    collected_at = datetime.now().astimezone()
    result: dict[str, Any] = {
        "session_id": session_id,
        "collected_at": collected_at.isoformat(),
        "correlation_keys": {
            "agent_id": agent.get("id") or agent.get("agent_id"),
            "agent_version": agent.get("version"),
            "model": (agent.get("model") or {})
            if isinstance(agent.get("model"), dict)
            else agent.get("model"),
            "environment_id": environment_id,
            "deployment_id": chain.session.get("deployment_id"),
            "metadata": chain.session.get("metadata"),
            "status": chain.session.get("status"),
            "resources": chain.session.get("resources"),
        },
        "event_counts": chain.event_counts,
        "event_total": len(events),
        "status_transitions": chain.status_transitions,
        "model_spans": chain.spans,
        "tool_calls": chain.tools,
        "compactions": chain.compactions,
        "errors": chain.errors,
        "threads": chain.threads,
        "usage_snapshots": chain.usage_snapshots,
        "work_items": chain.work_items,
        "work_stats": chain.work_stats,
        "sandbox": chain.sandbox,
        "economics": chain.economics,
        "staleness": _staleness(chain, collected_at),
        "compaction_windows": _compaction_windows(chain),
        "gaps": chain.gaps,
    }
    if raw_events:
        result["raw_events"] = events
    return result


def summarize(report: dict[str, Any]) -> str:
    keys = report["correlation_keys"]
    econ = report["economics"]
    tools = report["tool_calls"]
    latencies = [t["latency_s"] for t in tools if t["latency_s"] is not None]
    lines = [
        f"session {report['session_id']} status={keys['status']}",
        f"  agent={keys['agent_id']} v{keys['agent_version']} env={keys['environment_id']}",
        f"  events={report['event_total']} threads={len(report['threads'])} "
        f"spans={len(report['model_spans'])} tools={len(tools)} "
        f"compactions={len(report['compactions'])} errors={len(report['errors'])}",
        f"  list_cost=${econ['session_list_cost_usd']} "
        f"thread_sum=${econ['thread_list_cost_sum_usd']} "
        f"delta=${econ['session_minus_thread_sum_usd']}",
        f"  tokens={econ['span_token_sums']} "
        f"cache_read_fraction={econ['prompt_cache_read_fraction_of_input']}",
        f"  active_seconds={econ['active_seconds']} budget=${econ['budget_usd']}",
        f"  tool_latency_s min/med/max="
        f"{min(latencies) if latencies else None}/"
        f"{sorted(latencies)[len(latencies) // 2] if latencies else None}/"
        f"{max(latencies) if latencies else None}",
        f"  work_items={len(report['work_items'])} work_stats={report['work_stats']}",
        f"  sandbox={ {k: (len(v) if isinstance(v, list) else v) for k, v in report['sandbox'].items()} }",
    ]
    for gap in report["gaps"]:
        lines.append(f"  GAP: {gap}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--raw-events", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--no-work", action="store_true")
    args = parser.parse_args()
    report = collect(
        args.session_id,
        work=not args.no_work,
        sandbox=not args.no_sandbox,
        raw_events=args.raw_events,
    )
    print(summarize(report))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=str))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
