"""Build the seed bundle for the workstream-B workload.

Primitive: session message content. A Managed Agents session starts with an
empty sandbox and there is no native "seed this workspace" primitive, so the
fixture is delivered as one self-extracting bash command inside the initial
``user.message``. This module only packs the checked-in fixture; it is not a
runtime component.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_gen  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture" / "acme_billing"
WORKDIR = "/workspace/acme_billing"
BASELINE_NAME = ".grade_baseline.json"


def fixture_files() -> dict[str, bytes]:
    out: dict[str, bytes] = dict(fixture_gen.start_files())
    for path in sorted(FIXTURE.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(FIXTURE).as_posix()
        if "__pycache__" in rel or rel == BASELINE_NAME:
            continue
        out[rel] = path.read_bytes()
    return out


def baseline(files: dict[str, bytes]) -> dict[str, object]:
    return {
        "files": {
            name: hashlib.sha256(blob).hexdigest()
            for name, blob in files.items()
            if name.endswith(".py")
        }
    }


def tarball() -> bytes:
    files = fixture_files()
    files[BASELINE_NAME] = json.dumps(baseline(files), indent=2).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, blob in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            info.mode = 0o644
            info.mtime = 1787000000
            tar.addfile(info, io.BytesIO(blob))
    return buffer.getvalue()


def seed_command(workdir: str = WORKDIR) -> str:
    """Fallback seeding: the whole fixture inlined in the initial ``user.message``."""
    payload = base64.b64encode(tarball()).decode()
    return (
        f"mkdir -p {workdir} && cd {workdir} && "
        f"echo {payload} | base64 -d | tar xzf - && "
        f"ls -R . | head -40 && python3 grade.py | head -12"
    )


# ``mount_path`` is interpreted relative to the session's uploads root: a resource
# declared with mount_path=/workspace/seed/x.tar.gz lands at UPLOADS + that path
# (verified in session sesn_01RqxhRcrt9veLo3zuaV5sNy).
MOUNT_PATH = "/workspace/seed/acme_billing.tar.gz"
UPLOADS = "/mnt/session/uploads"
MOUNT = UPLOADS + MOUNT_PATH


def upload(client: Any) -> str:
    """Upload the fixture as a native file resource (cached in ``artifacts/file.json``).

    Primitive: ``sessions.resources`` of type ``file`` with a ``mount_path`` -- the
    native way to put bytes in a session sandbox without a repository checkout.
    """
    cache = HERE / "artifacts" / "file.json"
    blob = tarball()
    digest = hashlib.sha256(blob).hexdigest()
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("sha256") == digest:
            return str(data["file_id"])
    meta = client.beta.files.upload(
        file=("acme_billing.tar.gz", io.BytesIO(blob), "application/gzip")
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"file_id": meta.id, "sha256": digest}, indent=2))
    return str(meta.id)


def unpack_command(workdir: str = WORKDIR, mount: str = MOUNT) -> str:
    return (
        f"mkdir -p {workdir} && tar xzf {mount} -C {workdir} && "
        f"cd {workdir} && ls && python3 grade.py | head -12"
    )


def write_local(target: Path) -> Path:
    """Materialise the fixture (plus baseline) on the local filesystem."""
    import shutil
    import subprocess

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tarball())) as tar:
        subprocess.run(["true"], check=True)
        tar.extractall(target, filter="data")
    return target


if __name__ == "__main__":
    command = seed_command()
    print(f"bundle bytes: {len(tarball())}, seed command chars: {len(command)}")
