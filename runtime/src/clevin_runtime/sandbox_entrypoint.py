from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from anthropic import AsyncAnthropic

WORKDIR = "/workspace"
CREDENTIAL_HELPER_PATH = Path("/root/.local/bin/git-credential-clevin")
GIT_CONFIG_PATH = Path("/root/.gitconfig")

log = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def configure_git_credentials() -> None:
    CREDENTIAL_HELPER_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIAL_HELPER_PATH.write_text(
        """#!/bin/sh
if [ "$1" != "get" ]; then
    exit 0
fi
host=""
while IFS='=' read -r key value; do
    if [ "$key" = "host" ]; then
        host="$value"
    fi
done
if [ "$host" = "github.com" ] && [ -n "$GITHUB_TOKEN" ]; then
    printf '%s\\n' 'username=x-access-token'
    printf 'password=%s\\n' "$GITHUB_TOKEN"
fi
""",
        encoding="utf-8",
    )
    CREDENTIAL_HELPER_PATH.chmod(0o700)
    GIT_CONFIG_PATH.write_text(
        f"[credential]\n\thelper = {CREDENTIAL_HELPER_PATH}\n\tuseHttpPath = true\n",
        encoding="utf-8",
    )
    GIT_CONFIG_PATH.chmod(0o600)


async def run_work_item() -> None:
    environment_key = _required_env("ANTHROPIC_ENVIRONMENT_KEY")
    work_id = _required_env("ANTHROPIC_WORK_ID")
    environment_id = _required_env("ANTHROPIC_ENVIRONMENT_ID")
    session_id = _required_env("ANTHROPIC_SESSION_ID")
    work_secret = _required_env("ANTHROPIC_WORK_SECRET")
    max_idle = float(os.environ.get("APP_WORKER_IDLE_TIMEOUT_SECONDS", "120"))

    async with AsyncAnthropic(auth_token=environment_key) as client:
        worker = client.beta.environments.work.worker(
            environment_key=environment_key,
            workdir=WORKDIR,
            max_idle=max_idle,
            memory_sync_deletions="log_only",
        )
        await worker.handle_item(
            work_id=work_id,
            environment_id=environment_id,
            session_id=session_id,
            environment_key=environment_key,
            work_secret=work_secret,
        )


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("APP_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    configure_git_credentials()
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(run_work_item())
    stopping = False

    def cancel_worker() -> None:
        nonlocal stopping
        if not stopping:
            stopping = True
            task.cancel()

    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, cancel_worker)
        except NotImplementedError:
            continue
        installed.append(signum)

    try:
        await task
    except asyncio.CancelledError:
        if not stopping:
            raise
        log.info("worker cancelled")
    finally:
        for installed_signum in installed:
            loop.remove_signal_handler(installed_signum)


if __name__ == "__main__":
    asyncio.run(main())
