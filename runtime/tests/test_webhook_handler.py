from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from clevin_runtime import claude_webhook_handler as handler


class FakeRequest:
    def __init__(self, body: bytes = b"payload") -> None:
        self._body = body
        self.headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return self._body


def test_signature_rejection_does_not_log_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(
        "ANTHROPIC_WEBHOOK_SECRET",
        "whsec_MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    payload = b"sensitive-payload"

    with pytest.raises(HTTPException) as raised:
        handler._verify_webhook(payload, {})

    assert raised.value.status_code == 401
    assert "sensitive-payload" not in caplog.text


async def test_irrelevant_event_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        data=SimpleNamespace(type="session.updated", id="session_test")
    )
    monkeypatch.setattr(handler, "_verify_webhook", lambda *_: event)
    drain = AsyncMock()
    monkeypatch.setattr(handler, "_drain_work", drain)

    response = await handler.handle_webhook(cast(Any, FakeRequest()))

    assert response == {"status": "ignored", "event_type": "session.updated"}
    drain.assert_not_awaited()


async def test_run_started_event_drains_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        data=SimpleNamespace(
            type="session.status_run_started",
            id="session_test",
        )
    )
    monkeypatch.setattr(handler, "_verify_webhook", lambda *_: event)
    monkeypatch.setenv("ANTHROPIC_ENVIRONMENT_ID", "environment_test")
    drain = AsyncMock(return_value=[{"work_id": "work_test"}])
    monkeypatch.setattr(handler, "_drain_work", drain)

    response = await handler.handle_webhook(cast(Any, FakeRequest()))

    drain.assert_awaited_once_with("environment_test")
    assert response["status"] == "ok"
