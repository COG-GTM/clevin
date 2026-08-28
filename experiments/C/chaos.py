"""Workstream C fault-injection harness for the native Managed Agents runtime.

Primitives exercised: self-hosted `EnvironmentWorker` (poll / lease heartbeat /
force-stop), the sessions-side tool runner (`agent.tool_use` ->
`user.tool_result`), session event replay, and the `agent_toolset_20260401`
tool surface. Nothing here replaces a native primitive: the worker, poller,
tool runner and tool implementations are all the SDK's own; this module only
wraps the SDK-provided tools so a chosen tool call can be made to hang, fail,
return oversized output, or crash the worker process mid-call, and records what
the platform does in response.

The wrapper is required because the platform offers no way to make a *native*
tool misbehave on demand, and the recovery semantics under investigation are
only observable when it does.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import httpx
from anthropic.lib.tools._beta_functions import ToolError
from anthropic.lib.tools._tool_dispatch import run_runnable_tool
from anthropic.lib.tools.agent_toolset import (
    AgentToolContext,
    beta_agent_toolset_20260401,
)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
SMOKE_PREFIX = "CLEVIN_SMOKE_TEST"

log = logging.getLogger("chaos")


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


# --------------------------------------------------------------------------
# session helpers (native sessions API only)
# --------------------------------------------------------------------------


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def create_session(
    prompt: str,
    *,
    title: str,
    max_cost: str = "100",
    agent_id: str | None = None,
    agent_version: int | None = None,
    metadata: dict[str, str] | None = None,
    vault: bool = False,
) -> str:
    """Create a smoke-test session bound to the self-hosted environment."""
    c = client()
    agent_id = agent_id or env("CLEVIN_AGENT_ID")
    if agent_version is None:
        agent_version = max(v.version for v in c.beta.agents.versions.list(agent_id))
    session = c.beta.sessions.create(
        agent={"type": "agent", "id": agent_id, "version": agent_version},
        environment_id=env("CLEVIN_ENVIRONMENT_ID"),
        vault_ids=[env("CLEVIN_VAULT_ID")] if vault else [],
        budget={
            "type": "limit",
            "max_list_cost": {"amount": max_cost, "currency": "USD"},
        },
        metadata={"experiment": "clevin-swarm-C", **(metadata or {})},
        title=title,
        initial_events=[
            {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
        ],
    )
    return session.id


def dump_events(session_id: str, name: str) -> Path:
    """Persist the full server-side event history for later evidence quoting."""
    c = client()
    session = c.beta.sessions.retrieve(session_id)
    events = [
        e.model_dump(mode="json")
        for e in c.beta.sessions.events.list(session_id, order="asc")
    ]
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"{name}.json"
    path.write_text(
        json.dumps(
            {"session": session.model_dump(mode="json"), "events": events},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def summarize(session_id: str) -> list[str]:
    """A compact one-line-per-event view used in findings excerpts."""
    c = client()
    lines: list[str] = []
    for e in c.beta.sessions.events.list(session_id, order="asc"):
        d = e.model_dump(mode="json")
        kind = d.get("type")
        extra = ""
        if kind in ("agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use"):
            extra = f" name={d.get('name')} id={d.get('id')} input={json.dumps(d.get('input'))[:160]}"
        elif kind in ("user.tool_result", "user.custom_tool_result"):
            content = json.dumps(d.get("content"))[:200]
            extra = f" for={d.get('tool_use_id') or d.get('custom_tool_use_id')} is_error={d.get('is_error')} {content}"
        elif kind == "session.status_idle":
            extra = f" stop_reason={json.dumps(d.get('stop_reason'))}"
        elif kind in ("agent.message", "user.message"):
            text = " ".join(
                b.get("text", "")
                for b in (d.get("content") or [])
                if isinstance(b, dict)
            )
            extra = f" {text[:300]!r}"
        elif kind == "session.usage":
            extra = f" {json.dumps(d.get('usage'))[:200]}"
        lines.append(f"{d.get('created_at')} {kind}{extra}")
    return lines


def nudge_webhook(session_id: str, *, url: str | None = None) -> int:
    """Re-deliver a signed `session.status_run_started` webhook for a session.

    Primitive: lifecycle webhooks. The deployed Modal handler drains work with
    ``reclaim_older_than_ms=2000`` on every delivery, so a replayed (or delayed)
    delivery is the native way to get an unserved work item picked up by the
    production sandbox path. Used here (a) to hand back a work item this
    experiment claimed but must not serve, and (b) to test delayed worker
    startup and duplicate webhook delivery.
    """
    from standardwebhooks import Webhook  # local import: optional SDK extra

    url = url or os.environ.get(
        "CLEVIN_WEBHOOK_URL", "https://hrabbani-clevin--clevin-webhook.modal.run"
    )
    now = datetime.datetime.now(datetime.UTC)
    payload = json.dumps(
        {
            "type": "session.status_run_started",
            "id": f"wh_chaos_{uuid.uuid4().hex[:16]}",
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "data": {"type": "session.status_run_started", "id": session_id},
        }
    )
    msg_id = f"msg_chaos_{uuid.uuid4().hex[:16]}"
    signature = Webhook(env("ANTHROPIC_WEBHOOK_SECRET")).sign(msg_id, now, payload)
    response = httpx.post(
        url,
        content=payload,
        headers={
            "content-type": "application/json",
            "webhook-id": msg_id,
            "webhook-timestamp": str(int(now.timestamp())),
            "webhook-signature": signature,
        },
        timeout=120.0,
    )
    log.info(
        "webhook nudge session=%s status=%s body=%s",
        session_id,
        response.status_code,
        response.text[:300],
    )
    return response.status_code


# --------------------------------------------------------------------------
# fault-injecting tool wrapper
# --------------------------------------------------------------------------


@dataclass
class FaultConfig:
    mode: str = "none"
    tool: str = "bash"
    trigger: str = "CHAOS"  # only fire when the tool input contains this marker
    size_bytes: int = 2 * 1024 * 1024
    delay: float = 0.0
    occurrence: int = 1


class FaultTool:
    """Delegating wrapper around one SDK tool.

    Presents the same ``name`` / ``to_dict`` / ``call`` surface the SDK's
    session tool runner dispatches against, forwards to the real tool, and
    applies the configured fault when the tool input carries the trigger
    marker.
    """

    def __init__(self, inner: Any, cfg: FaultConfig) -> None:
        self._inner = inner
        self._cfg = cfg
        self._hits = 0

    @property
    def name(self) -> str:
        return str(self._inner.name)

    def to_dict(self) -> Any:
        return self._inner.to_dict()

    @property
    def close(self) -> Any:
        return getattr(self._inner, "close", None)

    def _armed(self, input: dict[str, object]) -> bool:
        if self._cfg.mode == "none" or self.name != self._cfg.tool:
            return False
        blob = json.dumps(input)
        if self._cfg.trigger and self._cfg.trigger not in blob:
            return False
        self._hits += 1
        return self._hits >= self._cfg.occurrence

    async def call(self, input: object) -> Any:
        data: dict[str, object] = dict(input) if isinstance(input, dict) else {}
        if not self._armed(data):
            return await run_runnable_tool(self._inner, data)
        mode = self._cfg.mode
        log.warning(
            "FAULT mode=%s tool=%s input=%s", mode, self.name, json.dumps(data)[:200]
        )
        if mode == "hang":
            await asyncio.sleep(3600)
            return "unreachable"
        if mode == "kill-before":
            # Worker dies before the tool runs: nothing happened in the sandbox.
            log.warning("FAULT kill-before: exiting worker process")
            os._exit(97)
        if mode == "kill-after-side-effect":
            # Run the real tool (side effect lands), then die before the result
            # is posted -- the crash window that decides at-least-once vs
            # at-most-once tool semantics.
            await run_runnable_tool(self._inner, data)
            marker = Path(
                os.environ.get("CHAOS_SIDE_EFFECT_LOG", "/tmp/chaos-side-effect.log")
            )
            with marker.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.time()} executed {json.dumps(data)[:200]}\n")
            log.warning(
                "FAULT kill-after-side-effect: tool ran, exiting before posting result"
            )
            os._exit(98)
        if mode == "raise":
            raise RuntimeError("chaos: injected tool implementation failure")
        if mode == "tool-error":
            raise ToolError("chaos: injected structured tool error")
        if mode == "oversized":
            return "A" * self._cfg.size_bytes
        if mode == "malformed":
            # A content shape the result event schema does not accept.
            return [
                {"type": "chaos_block", "not_text": {"deeply": ["nested", 1, None]}}
            ]  # type: ignore[list-item]
        if mode == "slow":
            await asyncio.sleep(self._cfg.delay)
            return await run_runnable_tool(self._inner, data)
        raise SystemExit(f"unknown fault mode {mode}")


def tools_factory(cfg: FaultConfig):
    def factory(ctx: AgentToolContext) -> list[Any]:
        return [FaultTool(t, cfg) for t in beta_agent_toolset_20260401(ctx)]

    return factory


# --------------------------------------------------------------------------
# worker entrypoint
# --------------------------------------------------------------------------


async def run_scoped_worker(args: argparse.Namespace) -> None:
    """Poll the shared self-hosted environment but only serve allowlisted sessions.

    The production environment is also served by the Modal webhook path and is
    shared with sibling experiments, so an unscoped local worker would race for
    -- and could steal -- other sessions' work. This loop uses the native
    poll/ack/stop endpoints directly: a work item for a session that is not in
    ``--session`` is deliberately left un-ack'd (the queue reclaims un-ack'd
    work) and a signed webhook delivery is replayed so the production Modal
    handler picks it up.
    """
    cfg = FaultConfig(
        mode=args.fault,
        tool=args.fault_tool,
        trigger=args.trigger,
        size_bytes=args.size_bytes,
        delay=args.delay,
        occurrence=args.occurrence,
    )
    allow_file = Path(args.allow_file) if args.allow_file else None
    static_allow = set(args.session or [])
    allow_tag = args.allow_metadata
    scoped = bool(static_allow or allow_file or allow_tag)

    def owns(session_id: str) -> bool:
        """Ownership test by session metadata, for sessions created after startup.

        A worker has to be polling before the session exists to win the claim
        race against the production Modal webhook path, so it cannot be handed
        the id up front. Session metadata is the only native per-session label
        readable at claim time.
        """
        if not allow_tag:
            return False
        key_name, _, want = allow_tag.partition("=")
        try:
            session = client().beta.sessions.retrieve(session_id)
        except Exception as exc:  # noqa: BLE001 - treat as foreign, but log why
            log.warning("metadata check failed session=%s error=%s", session_id, exc)
            return False
        metadata = session.metadata or {}
        return str(metadata.get(key_name)) == want

    def allowed(session_id: str | None) -> bool:
        """Allowlist check that tolerates the create/claim race.

        The work item becomes claimable the instant the session is created, so a
        tightly polling worker can claim it before the driver has written the id
        to the allowlist file; wait briefly for the id to show up before
        deciding the item belongs to somebody else.
        """
        if not scoped or session_id is None:
            return not scoped
        deadline = time.monotonic() + args.allow_wait
        while True:
            allow = set(static_allow)
            if allow_file and allow_file.exists():
                allow |= {
                    line.strip()
                    for line in allow_file.read_text().splitlines()
                    if line.strip()
                }
            if session_id in allow:
                return True
            if owns(session_id):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    key = env("ANTHROPIC_ENVIRONMENT_KEY")
    environment_id = env("CLEVIN_ENVIRONMENT_ID")
    worker_id = args.worker_id or f"chaos-{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + args.run_seconds
    served = 0
    handbacks: list[asyncio.Task[None]] = []

    async def _hand_back(session_id: str) -> None:
        """Return an accidentally claimed foreign work item to the production path.

        A claimed-but-un-ack'd item is only reclaimable once the claim is older
        than the handler's ``reclaim_older_than_ms`` (and, empirically, once this
        claim's lease has lapsed), so the signed webhook delivery is replayed
        several times over the lease TTL until the handler reports a spawn.
        """
        for delay in (3.0, 30.0, 95.0, 150.0):
            await asyncio.sleep(delay)
            try:
                await asyncio.to_thread(nudge_webhook, session_id)
            except Exception as e:  # noqa: BLE001 - best effort handback
                log.error("handback nudge failed session=%s error=%s", session_id, e)

    async with anthropic.AsyncAnthropic(auth_token=key) as c:
        work = c.beta.environments.work
        worker = c.beta.environments.work.worker(
            environment_id=environment_id,
            environment_key=key,
            workdir=str(workdir),
            tools=tools_factory(cfg),
            max_idle=args.max_idle,
            memory_sync_interval=None,
            worker_id=worker_id,
        )
        log.info(
            "scoped worker starting workdir=%s fault=%s allow=%s worker_id=%s",
            workdir,
            cfg.mode,
            sorted(static_allow) or (str(allow_file) if allow_file else "<any>"),
            worker_id,
        )
        while time.monotonic() < deadline:
            try:
                item = await work.poll(
                    environment_id, block_ms=999, anthropic_worker_id=worker_id
                )
            except anthropic.APIStatusError as e:
                log.warning("poll failed status=%s", e.status_code)
                await asyncio.sleep(2.0)
                continue
            if item is None:
                await asyncio.sleep(random.uniform(0.0, args.poll_gap))
                continue
            session_id = getattr(item.data, "id", None)
            log.info(
                "claimed work_id=%s type=%s session=%s",
                item.id,
                item.data.type,
                session_id,
            )
            if item.data.type != "session" or not allowed(session_id):
                log.warning(
                    "foreign work item released work_id=%s session=%s",
                    item.id,
                    session_id,
                )
                if session_id:
                    handbacks.append(asyncio.create_task(_hand_back(session_id)))
                continue
            await work.ack(item.id, environment_id=environment_id)
            try:
                await worker.handle_item(
                    work_id=item.id,
                    environment_id=item.environment_id,
                    session_id=session_id,
                    environment_key=key,
                    work_secret=item.secret,
                )
            except Exception as e:  # noqa: BLE001 - keep serving, record the failure
                log.error("handle_item raised %s: %s", type(e).__name__, e)
            served += 1
            log.info("finished work_id=%s served=%d", item.id, served)
            if args.once:
                break
    for task in handbacks:
        if not task.done():
            task.cancel()
    log.info("scoped worker exiting served=%d handbacks=%d", served, len(handbacks))


async def run_worker(args: argparse.Namespace) -> None:
    cfg = FaultConfig(
        mode=args.fault,
        tool=args.fault_tool,
        trigger=args.trigger,
        size_bytes=args.size_bytes,
        delay=args.delay,
        occurrence=args.occurrence,
    )
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    key = env("ANTHROPIC_ENVIRONMENT_KEY")
    async with anthropic.AsyncAnthropic(auth_token=key) as c:
        worker = c.beta.environments.work.worker(
            environment_id=env("CLEVIN_ENVIRONMENT_ID"),
            environment_key=key,
            workdir=str(workdir),
            tools=tools_factory(cfg),
            max_idle=args.max_idle,
            memory_sync_interval=None,
            worker_id=args.worker_id,
        )
        log.info(
            "worker starting workdir=%s fault=%s max_idle=%s worker_id=%s",
            workdir,
            cfg.mode,
            args.max_idle,
            args.worker_id,
        )
        try:
            await asyncio.wait_for(worker.run(), timeout=args.run_seconds)
        except asyncio.TimeoutError:
            log.info("worker run window elapsed after %ss", args.run_seconds)


def main() -> None:
    p = argparse.ArgumentParser(description="Workstream C chaos worker")
    p.add_argument("--workdir", default="/tmp/chaos-workspace")
    p.add_argument(
        "--fault",
        default="none",
        choices=[
            "none",
            "hang",
            "raise",
            "tool-error",
            "oversized",
            "malformed",
            "slow",
            "kill-before",
            "kill-after-side-effect",
        ],
    )
    p.add_argument("--fault-tool", default="bash")
    p.add_argument("--trigger", default="CHAOS")
    p.add_argument("--size-bytes", type=int, default=2 * 1024 * 1024)
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--occurrence", type=int, default=1)
    p.add_argument("--max-idle", type=float, default=60.0)
    p.add_argument("--run-seconds", type=float, default=600.0)
    p.add_argument("--worker-id", default=None)
    p.add_argument(
        "--session",
        action="append",
        help="only serve work for these session ids (repeatable); implies scoped mode",
    )
    p.add_argument(
        "--once", action="store_true", help="exit after serving one work item"
    )
    p.add_argument(
        "--allow-file",
        default=None,
        help="file of session ids (one per line) re-read on every claim",
    )
    p.add_argument(
        "--allow-wait",
        type=float,
        default=15.0,
        help="seconds to wait for a claimed session id to appear in the allowlist",
    )
    p.add_argument(
        "--allow-metadata",
        default=None,
        help="also serve sessions whose metadata matches key=value",
    )
    p.add_argument(
        "--poll-gap", type=float, default=0.4, help="max sleep between empty polls"
    )
    p.add_argument(
        "--unscoped",
        action="store_true",
        help="use the stock EnvironmentWorker.run() loop (serves any session)",
    )
    args = p.parse_args()
    logging.basicConfig(
        level=os.environ.get("CHAOS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Per-request httpx lines drown the lease/dispatch events these logs exist for.
    logging.getLogger("httpx").setLevel(
        os.environ.get("CHAOS_HTTP_LOG_LEVEL", "WARNING").upper()
    )
    asyncio.run(run_worker(args) if args.unscoped else run_scoped_worker(args))


if __name__ == "__main__":
    main()
