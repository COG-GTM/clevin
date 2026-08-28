from __future__ import annotations

from pathlib import Path

import dotenv
import modal

from clevin_runtime.config import (
    APP_LOG_LEVEL,
    APP_MEMORY_PATH,
    APP_WORKER_IDLE_TIMEOUT_SECONDS,
    MODAL_APP_NAME,
    PROJECT_ROOT,
    ROOT_ENV_PATH,
)

_ENTRYPOINT_SOURCE = Path(__file__).with_name("sandbox_entrypoint.py")
_ENTRYPOINT_DESTINATION = "/opt/clevin/sandbox_entrypoint.py"

sandbox_image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install(
        "bash",
        "ca-certificates",
        "git",
        "tar",
        "gzip",
        "bzip2",
        "xz-utils",
        "zip",
        "unzip",
    )
    .run_commands(
        f"install -d -m 1777 {APP_MEMORY_PATH}",
        "install -d -m 0755 /workspace /opt/clevin",
    )
    .uv_sync(
        uv_project_dir=str(PROJECT_ROOT / "runtime"),
        extra_options="--no-default-groups",
    )
    .add_local_file(
        _ENTRYPOINT_SOURCE,
        _ENTRYPOINT_DESTINATION,
        copy=True,
    )
    .entrypoint(["python", _ENTRYPOINT_DESTINATION])
    .env(
        {
            "APP_LOG_LEVEL": APP_LOG_LEVEL,
            "APP_WORKER_IDLE_TIMEOUT_SECONDS": str(APP_WORKER_IDLE_TIMEOUT_SECONDS),
        }
    )
)

app = modal.App(MODAL_APP_NAME)


@app.local_entrypoint()
def main() -> None:
    image_id = sandbox_image.build(app).object_id
    if ROOT_ENV_PATH.exists():
        dotenv.set_key(ROOT_ENV_PATH, "SANDBOX_IMAGE_ID", image_id, quote_mode="never")
    print(f"SANDBOX_IMAGE_ID={image_id}")
