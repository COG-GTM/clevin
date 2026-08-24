from __future__ import annotations

import logging

import modal
from fastapi import Request

from clevin_runtime.claude_webhook_handler import handle_webhook
from clevin_runtime.config import (
    APP_LOG_LEVEL,
    MODAL_APP_NAME,
    MODAL_CREDENTIALS_NAME,
    PROJECT_ROOT,
)

logging.basicConfig(level=APP_LOG_LEVEL)

_deployment_secret = modal.Secret.from_name(MODAL_CREDENTIALS_NAME)
_webhook_image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_sync(
        uv_project_dir=str(PROJECT_ROOT / "runtime"),
        extra_options="--no-default-groups",
    )
    .add_local_python_source("clevin_runtime", copy=True)
)

app = modal.App(MODAL_APP_NAME)


@app.function(image=_webhook_image, secrets=[_deployment_secret], timeout=300)
@modal.fastapi_endpoint(method="POST")
async def webhook(request: Request) -> dict[str, object]:
    return await handle_webhook(request)
