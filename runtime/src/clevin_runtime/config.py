from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import dotenv

_MODULE_PATH = Path(__file__).resolve()
PROJECT_ROOT = _MODULE_PATH.parents[3] if len(_MODULE_PATH.parents) > 3 else Path.cwd()
ROOT_ENV_PATH = PROJECT_ROOT / ".env"

MODAL_APP_NAME = "clevin"
MODAL_CREDENTIALS_NAME = "clevin-runtime"
MODAL_SESSIONS_VOLUME_NAME = f"{MODAL_APP_NAME}-sessions"
APP_SANDBOX_WORKDIR = "/workspace"
APP_MEMORY_PATH = "/mnt/memory"
APP_SANDBOX_TIMEOUT_SECONDS = 3600
APP_WORKER_IDLE_TIMEOUT_SECONDS = 120.0
APP_LOG_LEVEL = "INFO"


class ConfigurationError(ValueError):
    pass


def _required(
    source: Mapping[str, str | None], names: tuple[str, ...]
) -> dict[str, str]:
    missing = [name for name in names if not source.get(name)]
    if missing:
        joined = ", ".join(sorted(missing))
        raise ConfigurationError(f"missing required settings: {joined}")
    return {name: str(source[name]) for name in names}


@dataclass(frozen=True, slots=True)
class LocalSettings:
    environment_id: str
    environment_key: str
    webhook_secret: str | None
    sandbox_image_id: str
    github_token: str

    @classmethod
    def from_root_env(cls) -> LocalSettings:
        values = dotenv.dotenv_values(ROOT_ENV_PATH)
        required = _required(
            values,
            (
                "CLEVIN_ENVIRONMENT_ID",
                "ANTHROPIC_ENVIRONMENT_KEY",
                "SANDBOX_IMAGE_ID",
                "GITHUB_TOKEN",
            ),
        )
        webhook_secret = values.get("ANTHROPIC_WEBHOOK_SECRET")
        return cls(
            environment_id=required["CLEVIN_ENVIRONMENT_ID"],
            environment_key=required["ANTHROPIC_ENVIRONMENT_KEY"],
            webhook_secret=webhook_secret.strip() if webhook_secret else None,
            sandbox_image_id=required["SANDBOX_IMAGE_ID"],
            github_token=required["GITHUB_TOKEN"],
        )

    def deployment_environment(self) -> dict[str, str]:
        values = {
            "ANTHROPIC_ENVIRONMENT_ID": self.environment_id,
            "ANTHROPIC_ENVIRONMENT_KEY": self.environment_key,
            "SANDBOX_IMAGE_ID": self.sandbox_image_id,
            "GITHUB_TOKEN": self.github_token,
        }
        if self.webhook_secret is not None:
            values["ANTHROPIC_WEBHOOK_SECRET"] = self.webhook_secret
        return values


@dataclass(frozen=True, slots=True)
class ClientSettings:
    api_key: str
    agent_id: str
    agent_version: int
    environment_id: str
    vault_id: str
    memory_store_id: str
    workspace_id: str | None

    @classmethod
    def from_root_env(cls) -> ClientSettings:
        values = dotenv.dotenv_values(ROOT_ENV_PATH)
        required = _required(
            values,
            (
                "ANTHROPIC_API_KEY",
                "CLEVIN_AGENT_ID",
                "CLEVIN_AGENT_VERSION",
                "CLEVIN_ENVIRONMENT_ID",
                "CLEVIN_VAULT_ID",
                "CLEVIN_MEMORY_STORE_ID",
            ),
        )
        try:
            agent_version = int(required["CLEVIN_AGENT_VERSION"])
        except ValueError:
            raise ConfigurationError(
                "CLEVIN_AGENT_VERSION must be a positive integer"
            ) from None
        if agent_version < 1:
            raise ConfigurationError("CLEVIN_AGENT_VERSION must be a positive integer")
        workspace_id = values.get("ANTHROPIC_WORKSPACE_ID")
        return cls(
            api_key=required["ANTHROPIC_API_KEY"],
            agent_id=required["CLEVIN_AGENT_ID"],
            agent_version=agent_version,
            environment_id=required["CLEVIN_ENVIRONMENT_ID"],
            vault_id=required["CLEVIN_VAULT_ID"],
            memory_store_id=required["CLEVIN_MEMORY_STORE_ID"],
            workspace_id=workspace_id.strip() if workspace_id else None,
        )


@dataclass(frozen=True, slots=True)
class DeploymentSettings:
    environment_id: str
    environment_key: str
    webhook_secret: str
    sandbox_image_id: str
    github_token: str

    @classmethod
    def from_environment(cls) -> DeploymentSettings:
        required = _required(
            os.environ,
            (
                "ANTHROPIC_ENVIRONMENT_ID",
                "ANTHROPIC_ENVIRONMENT_KEY",
                "ANTHROPIC_WEBHOOK_SECRET",
                "SANDBOX_IMAGE_ID",
                "GITHUB_TOKEN",
            ),
        )
        return cls(
            environment_id=required["ANTHROPIC_ENVIRONMENT_ID"],
            environment_key=required["ANTHROPIC_ENVIRONMENT_KEY"],
            webhook_secret=required["ANTHROPIC_WEBHOOK_SECRET"],
            sandbox_image_id=required["SANDBOX_IMAGE_ID"],
            github_token=required["GITHUB_TOKEN"],
        )
