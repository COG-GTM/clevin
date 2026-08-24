from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from clevin_runtime import claude_webhook_handler as handler
from clevin_runtime import sandbox_entrypoint
from clevin_runtime.sandbox_runtime import SandboxResult


def _deployment_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ANTHROPIC_ENVIRONMENT_ID": "environment_test",
        "ANTHROPIC_ENVIRONMENT_KEY": "environment-key",
        "ANTHROPIC_WEBHOOK_SECRET": "webhook-secret",
        "SANDBOX_IMAGE_ID": "image-test",
        "GITHUB_TOKEN": "github-token",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


async def test_queue_drain_forwards_work_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deployment_environment(monkeypatch)
    work_secret = "work" + "-secret"
    github_token = "github" + "-token"
    work_item = SimpleNamespace(
        id="work_test",
        environment_id="environment_test",
        secret=work_secret,
        data=SimpleNamespace(type="session", id="session_test"),
    )

    async def items():
        yield work_item

    poller = MagicMock(return_value=items())
    work_resource = SimpleNamespace(poller=poller)
    client = SimpleNamespace(
        beta=SimpleNamespace(environments=SimpleNamespace(work=work_resource))
    )

    class ClientContext:
        beta = client.beta

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(
        handler.anthropic,
        "AsyncAnthropic",
        lambda **_: ClientContext(),
    )
    get_or_create = AsyncMock(return_value=SandboxResult("sandbox_test", True))
    runtime = SimpleNamespace(get_or_create=get_or_create)
    monkeypatch.setattr(handler, "SandboxRuntime", lambda: runtime)

    result = await handler._drain_work("environment_test")

    poller.assert_called_once_with(
        environment_id="environment_test",
        environment_key="environment-key",
        block_ms=None,
        reclaim_older_than_ms=2000,
        drain=True,
        auto_stop=False,
    )
    get_or_create.assert_awaited_once_with(
        "session_test",
        environment_id="environment_test",
        work_id="work_test",
        environment_key="environment-key",
        work_secret=work_secret,
        github_token=github_token,
        image_id="image-test",
    )
    assert result == [
        {
            "work_id": "work_test",
            "session_id": "session_test",
            "sandbox_id": "sandbox_test",
            "created": True,
        }
    ]


async def test_worker_uses_native_tools_and_explicit_item_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "ANTHROPIC_ENVIRONMENT_KEY": "environment-key",
        "ANTHROPIC_WORK_ID": "work_test",
        "ANTHROPIC_ENVIRONMENT_ID": "environment_test",
        "ANTHROPIC_SESSION_ID": "session_test",
        "ANTHROPIC_WORK_SECRET": "work-secret",
        "APP_WORKER_IDLE_TIMEOUT_SECONDS": "90",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    handle_item = AsyncMock()
    worker = SimpleNamespace(handle_item=handle_item)
    worker_factory = MagicMock(return_value=worker)
    client = SimpleNamespace(
        beta=SimpleNamespace(
            environments=SimpleNamespace(work=SimpleNamespace(worker=worker_factory))
        )
    )
    auth = MagicMock()

    class ClientContext:
        def __init__(self, *, auth_token: str) -> None:
            auth(auth_token)

        async def __aenter__(self):
            return client

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(sandbox_entrypoint, "AsyncAnthropic", ClientContext)

    await sandbox_entrypoint.run_work_item()

    auth.assert_called_once_with("environment-key")
    worker_factory.assert_called_once_with(
        environment_key="environment-key",
        workdir="/workspace",
        max_idle=90.0,
        memory_sync_deletions="log_only",
    )
    assert "tools" not in worker_factory.call_args.kwargs
    handle_item.assert_awaited_once_with(
        work_id="work_test",
        environment_id="environment_test",
        session_id="session_test",
        environment_key="environment-key",
        work_secret=values["ANTHROPIC_WORK_SECRET"],
    )
