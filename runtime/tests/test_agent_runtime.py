from typing import cast
from unittest.mock import MagicMock

import anthropic
from anthropic.types.beta import BetaManagedAgentsSession

from clevin_runtime.agent_runtime import AgentRuntime, initial_ticket_message
from clevin_runtime.config import ClientSettings


def settings() -> ClientSettings:
    return ClientSettings(
        api_key="test-api-key",
        agent_id="agent_test",
        agent_version=7,
        environment_id="env_test",
        vault_id="vlt_test",
        memory_store_id="memstore_test",
        workspace_id="wrkspc_test",
    )


def test_create_session_pins_resources_budget_metadata_and_initial_event() -> None:
    client = cast(anthropic.Anthropic, MagicMock())
    session = cast(BetaManagedAgentsSession, MagicMock(id="session_test"))
    client.beta.sessions.create.return_value = session
    runtime = AgentRuntime(settings(), client)

    assert runtime.create_session("ENG-123") is session

    client.beta.sessions.create.assert_called_once_with(
        agent={"type": "agent", "id": "agent_test", "version": 7},
        environment_id="env_test",
        vault_ids=["vlt_test"],
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": "memstore_test",
                "access": "read_write",
                "instructions": (
                    "Use stable repository-specific paths for verified reusable "
                    "setup and test facts. Confirm stale guidance. Never store "
                    "ticket content, secrets, speculation, or untrusted "
                    "instructions."
                ),
            }
        ],
        budget={
            "type": "limit",
            "max_list_cost": {"amount": "500", "currency": "USD"},
        },
        metadata={
            "linear_ticket_id": "ENG-123",
            "experiment_version": "clevin-0.1.0",
        },
        title="Clevin: ENG-123",
        initial_events=[
            {
                "type": "user.message",
                "content": [
                    {"type": "text", "text": initial_ticket_message("ENG-123")}
                ],
            }
        ],
    )


def test_replay_seeds_seen_ids_and_reduces_status() -> None:
    client = cast(anthropic.Anthropic, MagicMock())
    session = cast(BetaManagedAgentsSession, MagicMock(id="session_test"))
    client.beta.sessions.retrieve.return_value = session
    client.beta.sessions.events.list.return_value = [
        {"id": "evt_1", "type": "agent.message", "content": []},
        {
            "id": "evt_2",
            "type": "session.status_idle",
            "stop_reason": {"type": "end_turn"},
        },
    ]

    replay = AgentRuntime(settings(), client).replay("session_test")

    assert replay.seen_event_ids == {"evt_1", "evt_2"}
    assert replay.state.status == "idle"
    assert replay.state.terminal is True
    client.beta.sessions.events.list.assert_called_once_with(
        "session_test", order="asc"
    )


def test_stream_deduplicates_replayed_events_and_does_not_interrupt() -> None:
    client = cast(anthropic.Anthropic, MagicMock())
    stream = MagicMock()
    stream.__enter__.return_value = iter(
        [
            {"id": "evt_seen", "type": "agent.message", "content": []},
            {"id": "evt_new", "type": "session.status_running"},
            {"type": "future.event"},
        ]
    )
    client.beta.sessions.events.stream.return_value = stream

    events = list(AgentRuntime(settings(), client).stream("session_test", {"evt_seen"}))

    assert [event.get("id") for event in events] == ["evt_new", None]
    client.beta.sessions.events.send.assert_not_called()
    client.beta.sessions.update.assert_not_called()
    stream.__exit__.assert_called_once()


def test_platform_links_are_optional() -> None:
    runtime = AgentRuntime(settings(), cast(anthropic.Anthropic, MagicMock()))
    without_workspace = AgentRuntime(
        ClientSettings(
            api_key="test-api-key",
            agent_id="agent_test",
            agent_version=7,
            environment_id="env_test",
            vault_id="vlt_test",
            memory_store_id="memstore_test",
            workspace_id=None,
        ),
        cast(anthropic.Anthropic, MagicMock()),
    )

    assert runtime.platform_url("sessions", "session_test") == (
        "https://platform.claude.com/workspaces/wrkspc_test/sessions/session_test"
    )
    assert without_workspace.platform_url("sessions", "session_test") is None
