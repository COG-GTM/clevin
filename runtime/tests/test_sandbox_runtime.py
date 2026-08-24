from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from clevin_runtime import sandbox_runtime
from clevin_runtime.sandbox_runtime import (
    SandboxRuntime,
    session_volume_sub_path,
    validate_session_id,
)


@pytest.mark.parametrize(
    "session_id",
    ("../escape", "session/escape", "", ".", "session id"),
)
def test_volume_path_rejects_invalid_session_ids(session_id: str) -> None:
    with pytest.raises(ValueError, match="invalid session ID"):
        session_volume_sub_path(session_id)


def test_volume_path_uses_validated_session_namespace() -> None:
    session_id = "session_01-test"
    assert validate_session_id(session_id) == session_id
    assert session_volume_sub_path(session_id) == f"/sessions/{session_id}"


def test_dashboard_urls_use_configured_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODAL_WORKSPACE_SLUG", "workspace-test")
    monkeypatch.setenv("MODAL_ENVIRONMENT", "clevin")

    sandbox_url, volume_url = SandboxRuntime().dashboard_urls("session_test", "sb-test")

    assert sandbox_url == (
        "https://modal.com/apps/workspace-test/clevin/deployed/clevin"
        "?activeTab=sandboxes&sandboxId=sb-test"
    )
    assert volume_url == (
        "https://modal.com/storage/workspace-test/clevin/volumes/"
        "clevin-sessions/sessions/session_test"
    )


async def test_live_sandbox_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SandboxRuntime()
    live = SimpleNamespace(object_id="sandbox_live")
    find_live = AsyncMock(return_value=live)
    create = AsyncMock()
    monkeypatch.setattr(runtime, "find_live", find_live)
    monkeypatch.setattr(runtime, "create", create)

    result = await runtime.get_or_create(
        "session_test",
        environment_id="environment_test",
        work_id="work_test",
        environment_key="environment-key",
        work_secret="work" + "-secret",
        github_token="github" + "-token",
        image_id="image-test",
    )

    assert result.sandbox_id == "sandbox_live"
    assert result.created is False
    find_live.assert_awaited_once_with("session_test")
    cast(Any, create).assert_not_awaited()


async def test_create_mounts_session_volume_and_injects_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = object()
    mounted_volume = object()
    volume = SimpleNamespace(with_mount_options=MagicMock(return_value=mounted_volume))
    lookup = AsyncMock(return_value=app)
    from_name = MagicMock(return_value=volume)
    from_dict = MagicMock(return_value="modal-secret")
    from_id = MagicMock(return_value="modal-image")
    create = AsyncMock(return_value=SimpleNamespace(object_id="sandbox_test"))
    monkeypatch.setattr(
        sandbox_runtime.modal,
        "App",
        SimpleNamespace(lookup=SimpleNamespace(aio=lookup)),
    )
    monkeypatch.setattr(
        sandbox_runtime.modal,
        "Volume",
        SimpleNamespace(from_name=from_name),
    )
    monkeypatch.setattr(
        sandbox_runtime.modal,
        "Secret",
        SimpleNamespace(from_dict=from_dict),
    )
    monkeypatch.setattr(
        sandbox_runtime.modal,
        "Image",
        SimpleNamespace(from_id=from_id),
    )
    monkeypatch.setattr(
        sandbox_runtime.modal,
        "Sandbox",
        SimpleNamespace(create=SimpleNamespace(aio=create)),
    )

    await SandboxRuntime().create(
        "session_test",
        environment_id="environment_test",
        work_id="work_test",
        environment_key="environment" + "-key",
        work_secret="work" + "-secret",
        github_token="github" + "-token",
        image_id="image-test",
    )

    volume.with_mount_options.assert_called_once_with(sub_path="/sessions/session_test")
    from_dict.assert_called_once_with(
        {
            "ANTHROPIC_ENVIRONMENT_KEY": "environment-key",
            "ANTHROPIC_WORK_SECRET": "work-secret",
            "GITHUB_TOKEN": "github-token",
        }
    )
    call = create.await_args.kwargs
    assert call["volumes"] == {"/workspace": mounted_volume}
    assert call["env"] == {
        "ANTHROPIC_SESSION_ID": "session_test",
        "ANTHROPIC_ENVIRONMENT_ID": "environment_test",
        "ANTHROPIC_WORK_ID": "work_test",
    }
    assert call["secrets"] == ["modal-secret"]
    assert call["timeout"] == 3600
    assert "idle_timeout" not in call
    assert call["experimental_options"] == {"enable_termination_grace_period": True}
    assert "block_network" not in call
    assert "outbound_cidr_allowlist" not in call
