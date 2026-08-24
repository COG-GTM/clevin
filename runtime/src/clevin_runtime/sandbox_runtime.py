from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import modal
from modal.config import config as modal_config

from clevin_runtime.config import (
    APP_SANDBOX_TIMEOUT_SECONDS,
    APP_SANDBOX_WORKDIR,
    MODAL_APP_NAME,
    MODAL_SESSIONS_VOLUME_NAME,
)

_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    sandbox_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    sandbox_id: str | None
    status: str
    volume_path: str
    sandbox_url: str | None
    volume_url: str | None


def validate_session_id(session_id: str) -> str:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid session ID")
    return session_id


def session_volume_sub_path(session_id: str) -> str:
    return f"/sessions/{validate_session_id(session_id)}"


class SandboxRuntime:
    async def find_live(self, session_id: str) -> modal.Sandbox | None:
        name = validate_session_id(session_id)
        try:
            sandbox = await modal.Sandbox.from_name.aio(MODAL_APP_NAME, name=name)
        except modal.exception.NotFoundError:
            return None
        return sandbox if await sandbox.poll.aio() is None else None

    def dashboard_urls(
        self, session_id: str, sandbox_id: str | None
    ) -> tuple[str | None, str | None]:
        name = validate_session_id(session_id)
        workspace = os.environ.get("MODAL_WORKSPACE_SLUG")
        environment = os.environ.get("MODAL_ENVIRONMENT") or modal_config.get(
            "environment"
        )
        if not workspace or not environment:
            return None, None
        app_url = "/".join(
            (
                "https://modal.com/apps",
                quote(workspace, safe=""),
                quote(str(environment), safe=""),
                "deployed",
                quote(MODAL_APP_NAME, safe=""),
            )
        )
        sandbox_url = None
        if sandbox_id is not None:
            query = urlencode({"activeTab": "sandboxes", "sandboxId": sandbox_id})
            sandbox_url = f"{app_url}?{query}"
        volume_url = "/".join(
            (
                "https://modal.com/storage",
                quote(workspace, safe=""),
                quote(str(environment), safe=""),
                "volumes",
                quote(MODAL_SESSIONS_VOLUME_NAME, safe=""),
                "sessions",
                quote(name, safe=""),
            )
        )
        return sandbox_url, volume_url

    async def snapshot(self, session_id: str) -> SandboxSnapshot:
        name = validate_session_id(session_id)
        sandbox = await self.find_live(name)
        sandbox_id = sandbox.object_id if sandbox is not None else None
        sandbox_url, volume_url = self.dashboard_urls(name, sandbox_id)
        return SandboxSnapshot(
            sandbox_id=sandbox_id,
            status="running" if sandbox is not None else "stopped",
            volume_path=(
                f"{MODAL_SESSIONS_VOLUME_NAME}{session_volume_sub_path(name)}"
            ),
            sandbox_url=sandbox_url,
            volume_url=volume_url,
        )

    async def create(
        self,
        session_id: str,
        *,
        environment_id: str,
        work_id: str,
        environment_key: str,
        work_secret: str,
        github_token: str,
        image_id: str,
    ) -> modal.Sandbox:
        name = validate_session_id(session_id)
        app = await modal.App.lookup.aio(MODAL_APP_NAME, create_if_missing=True)
        volume = modal.Volume.from_name(
            MODAL_SESSIONS_VOLUME_NAME,
            create_if_missing=True,
            version=2,
        ).with_mount_options(sub_path=session_volume_sub_path(name))
        secret = modal.Secret.from_dict(
            {
                "ANTHROPIC_ENVIRONMENT_KEY": environment_key,
                "ANTHROPIC_WORK_SECRET": work_secret,
                "GITHUB_TOKEN": github_token,
            }
        )
        return await modal.Sandbox.create.aio(
            app=app,
            name=name,
            image=modal.Image.from_id(image_id),
            timeout=APP_SANDBOX_TIMEOUT_SECONDS,
            workdir=APP_SANDBOX_WORKDIR,
            volumes={APP_SANDBOX_WORKDIR: volume},
            env={
                "ANTHROPIC_SESSION_ID": name,
                "ANTHROPIC_ENVIRONMENT_ID": environment_id,
                "ANTHROPIC_WORK_ID": work_id,
            },
            secrets=[secret],
            experimental_options={"enable_termination_grace_period": True},
        )

    async def get_or_create(
        self,
        session_id: str,
        *,
        environment_id: str,
        work_id: str,
        environment_key: str,
        work_secret: str,
        github_token: str,
        image_id: str,
    ) -> SandboxResult:
        existing = await self.find_live(session_id)
        if existing is not None:
            return SandboxResult(sandbox_id=existing.object_id, created=False)
        sandbox = await self.create(
            session_id,
            environment_id=environment_id,
            work_id=work_id,
            environment_key=environment_key,
            work_secret=work_secret,
            github_token=github_token,
            image_id=image_id,
        )
        return SandboxResult(sandbox_id=sandbox.object_id, created=True)
