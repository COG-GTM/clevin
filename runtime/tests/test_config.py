import pytest

from clevin_runtime import config


def test_client_settings_are_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config.dotenv,
        "dotenv_values",
        lambda _path: {
            "ANTHROPIC_API_KEY": "secret",
            "CLEVIN_AGENT_ID": "agent_test",
            "CLEVIN_AGENT_VERSION": "7",
            "CLEVIN_ENVIRONMENT_ID": "env_test",
            "CLEVIN_VAULT_ID": "vlt_test",
            "CLEVIN_MEMORY_STORE_ID": "memstore_test",
            "ANTHROPIC_WORKSPACE_ID": "wrkspc_test",
        },
    )

    settings = config.ClientSettings.from_root_env()

    assert settings.agent_version == 7
    assert settings.workspace_id == "wrkspc_test"


def test_local_deployment_can_precede_webhook_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config.dotenv,
        "dotenv_values",
        lambda _path: {
            "CLEVIN_ENVIRONMENT_ID": "env_test",
            "ANTHROPIC_ENVIRONMENT_KEY": "environment-secret",
            "SANDBOX_IMAGE_ID": "im_test",
            "GITHUB_TOKEN": "github-secret",
        },
    )

    values = config.LocalSettings.from_root_env().deployment_environment()

    assert "ANTHROPIC_WEBHOOK_SECRET" not in values
    assert values["ANTHROPIC_ENVIRONMENT_ID"] == "env_test"


def test_configuration_errors_never_include_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config.dotenv,
        "dotenv_values",
        lambda _path: {"ANTHROPIC_API_KEY": "must-not-appear"},
    )

    with pytest.raises(config.ConfigurationError) as error:
        config.ClientSettings.from_root_env()

    assert "must-not-appear" not in str(error.value)
    assert "CLEVIN_AGENT_ID" in str(error.value)
