"""Run one workstream-C fault-injection case end to end.

Primitives: sessions API (create / events.list) + the self-hosted
`EnvironmentWorker` poller. The driver starts `chaos.py` as a separate worker
process (faults such as `kill-*` terminate that process, so it must not be this
one), creates a session against the temporary harness agent, waits, and writes
the full server-side event history plus worker stderr to `artifacts/`.

Usage:
  uv run --project ../../runtime python run_case.py --name c1-local-worker \
      --fault none --prompt "..." --wait 120
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos  # noqa: E402
import harness_agent  # noqa: E402

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"


OWNER_TAG = "experiment=clevin-swarm-C"


def start_worker(
    name: str, extra: list[str], workdir: str, session_id: str | None = None
) -> tuple[subprocess.Popen[bytes], Path]:
    """Launch an ownership-scoped chaos worker.

    The worker must already be polling when the session is created, otherwise
    the production Modal webhook path claims the work item first and no fault is
    injected. Since the session id does not exist yet, ownership is decided from
    session metadata; anything else is handed straight back.
    """
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    logpath = ARTIFACTS / f"{name}-worker.log"
    logfile = logpath.open("wb")
    cmd = [
        sys.executable,
        str(HERE / "chaos.py"),
        "--workdir",
        workdir,
        "--allow-metadata",
        OWNER_TAG,
        "--allow-wait",
        "0",
        "--once",
        *(["--session", session_id] if session_id else []),
        *extra,
    ]
    proc = subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.STDOUT)
    return proc, logpath


def wait_and_dump(
    session_id: str,
    name: str,
    wait: float,
    *,
    procs: list[subprocess.Popen[bytes]] | None = None,
    restart: list[str] | None = None,
    workdir: str = "/tmp/chaos-workspace",
    session_scope: str | None = None,
) -> dict[str, object]:
    """Poll session events until a terminal idle state, restarting the worker once.

    ``restart`` models "a new worker comes back after the old one died": the
    replacement claims whatever the queue still holds for the session, which is
    how recovery is supposed to happen without custom orchestration.
    """
    c = chaos.client()
    deadline = time.time() + wait
    last = ""
    restarted: dict[str, object] = {}
    while time.time() < deadline:
        current = procs[-1] if procs else None
        if (
            restart is not None
            and current is not None
            and current.poll() is not None
            and not restarted
            and session_scope is not None
        ):
            new_proc, new_log = start_worker(
                f"{name}-restart", restart, workdir, session_scope
            )
            procs.append(new_proc)
            restarted = {
                "worker_died_exit": current.returncode,
                "restart_pid": new_proc.pid,
                "restart_log": str(new_log),
                "restart_delay_s": round(time.time() - (deadline - wait), 1),
            }
            print(
                f"worker died exit={current.returncode}; restarted pid={new_proc.pid}",
                flush=True,
            )
        events = list(c.beta.sessions.events.list(session_id, order="asc"))
        tail = events[-1].model_dump(mode="json") if events else {}
        line = f"{tail.get('type')} {json.dumps(tail.get('stop_reason'))}"
        if line != last:
            print(f"[{int(time.time() - (deadline - wait))}s] {line}", flush=True)
            last = line
        if tail.get("type") == "session.status_idle":
            stop = tail.get("stop_reason") or {}
            if stop.get("type") in ("budget_reached", "end_turn", "error", "max_turns"):
                break
        time.sleep(5)
    path = chaos.dump_events(session_id, name)
    return {"artifact": str(path), **restarted}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--wait", type=float, default=180.0)
    p.add_argument(
        "--no-worker",
        action="store_true",
        help="rely on the Modal webhook path instead",
    )
    p.add_argument("--workdir", default="/tmp/chaos-workspace")
    p.add_argument("--budget", default="100")
    p.add_argument("--production-agent", action="store_true")
    p.add_argument(
        "--restart",
        default=None,
        help="if the worker process dies, restart it once with these args (space separated)",
    )
    p.add_argument("worker_args", nargs="*", help="extra args forwarded to chaos.py")
    args = p.parse_args()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    agent_id = None
    if not args.production_agent:
        agent_id, _ = harness_agent.ensure_agent()

    procs: list[subprocess.Popen[bytes]] = []
    if not args.no_worker:
        proc, logpath = start_worker(args.name, list(args.worker_args), args.workdir)
        procs.append(proc)
        print(f"worker pid={proc.pid} log={logpath}", flush=True)
        time.sleep(4.0)

    session_id = chaos.create_session(
        args.prompt,
        title=f"clevin-swarm-C {args.name}",
        max_cost=args.budget,
        agent_id=agent_id,
        metadata={"case": args.name, "experiment": "clevin-swarm-C"},
    )
    print(f"session {session_id}", flush=True)
    try:
        out = wait_and_dump(
            session_id,
            args.name,
            args.wait,
            procs=procs,
            restart=args.restart.split() if args.restart is not None else None,
            workdir=args.workdir,
            session_scope=session_id,
        )
    finally:
        for worker_proc in procs:
            if worker_proc.poll() is None:
                worker_proc.terminate()
                try:
                    worker_proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    worker_proc.kill()
            print(
                f"worker pid={worker_proc.pid} exit={worker_proc.returncode}",
                flush=True,
            )
    print(json.dumps(out))
    for line in chaos.summarize(session_id):
        print(line)


if __name__ == "__main__":
    main()
