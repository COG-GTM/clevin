from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TypedDict

import anthropic
from anthropic.types.beta import UnwrapWebhookEvent
from fastapi import HTTPException, Request

from clevin_runtime.config import DeploymentSettings
from clevin_runtime.sandbox_runtime import SandboxRuntime

log = logging.getLogger(__name__)


class WorkResult(TypedDict, total=False):
    session_id: str
    work_id: str
    sandbox_id: str
    created: bool
    error: str


def _verify_webhook(raw_body: bytes, headers: Mapping[str, str]) -> UnwrapWebhookEvent:
    try:
        payload = raw_body.decode("utf-8")
        return anthropic.Anthropic().beta.webhooks.unwrap(
            payload,
            headers=headers,
            key=os.environ["ANTHROPIC_WEBHOOK_SECRET"],
        )
    except Exception:
        log.warning("webhook signature verification failed")
        raise HTTPException(
            status_code=401,
            detail="signature verification failed",
        ) from None


async def _drain_work(environment_id: str) -> list[WorkResult]:
    settings = DeploymentSettings.from_environment()
    runtime = SandboxRuntime()
    results: list[WorkResult] = []
    client = anthropic.AsyncAnthropic(auth_token=settings.environment_key)
    async with client:
        async for work in client.beta.environments.work.poller(
            environment_id=environment_id,
            environment_key=settings.environment_key,
            block_ms=None,
            reclaim_older_than_ms=2000,
            drain=True,
            auto_stop=False,
        ):
            if work.data.type != "session":
                continue
            session_id = work.data.id
            try:
                if not work.secret:
                    raise RuntimeError("work item secret is required")
                sandbox = await runtime.get_or_create(
                    session_id,
                    environment_id=work.environment_id,
                    work_id=work.id,
                    environment_key=settings.environment_key,
                    work_secret=work.secret,
                    github_token=settings.github_token,
                    image_id=settings.sandbox_image_id,
                )
            except Exception as error:
                log.error(
                    "sandbox launch failed for work=%s session=%s error=%s",
                    work.id,
                    session_id,
                    type(error).__name__,
                )
                results.append(
                    {
                        "work_id": work.id,
                        "session_id": session_id,
                        "error": type(error).__name__,
                    }
                )
            else:
                results.append(
                    {
                        "work_id": work.id,
                        "session_id": session_id,
                        "sandbox_id": sandbox.sandbox_id,
                        "created": sandbox.created,
                    }
                )
    return results


async def handle_webhook(request: Request) -> dict[str, object]:
    raw_body = await request.body()
    event = _verify_webhook(raw_body, request.headers)
    event_type = event.data.type
    if event_type != "session.status_run_started":
        return {"status": "ignored", "event_type": event_type}

    environment_id = os.environ.get("ANTHROPIC_ENVIRONMENT_ID")
    if not environment_id:
        raise RuntimeError("ANTHROPIC_ENVIRONMENT_ID is required")
    spawned = await _drain_work(environment_id)
    return {"status": "ok", "event_type": event_type, "spawned": spawned}
