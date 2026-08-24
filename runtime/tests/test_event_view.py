from clevin_runtime.event_view import EventState, event_id, reduce_event


def test_reduces_status_and_budget_reached() -> None:
    state = EventState()

    blocks = reduce_event(
        {
            "id": "evt_1",
            "type": "session.status_idle",
            "stop_reason": {"type": "budget_reached"},
        },
        state,
    )

    assert state.status == "idle"
    assert state.stop_reason == "budget_reached"
    assert state.terminal is True
    assert "budget_reached" in blocks[0].body


def test_requires_action_idle_is_not_terminal() -> None:
    state = EventState()

    reduce_event(
        {
            "id": "evt_action",
            "type": "session.status_idle",
            "stop_reason": {"type": "requires_action"},
        },
        state,
    )

    assert state.status == "idle"
    assert state.stop_reason == "requires_action"
    assert state.terminal is False


def test_reduces_usage_and_redacts_secrets() -> None:
    state = EventState()

    blocks = reduce_event(
        {
            "id": "evt_2",
            "type": "session.usage",
            "usage": {
                "input_tokens": 42,
                "list_cost": {"amount": "123", "currency": "USD"},
            },
            "budget": {
                "type": "limit",
                "max_list_cost": {"amount": "500", "currency": "USD"},
                "token": "must-not-render",
            },
        },
        state,
    )

    assert state.usage["list_cost"]["amount"] == "123"
    assert '"input_tokens": 42' in blocks[0].body
    assert "must-not-render" not in blocks[0].body
    assert "[REDACTED]" in blocks[0].body


def test_renders_tool_events_and_redacts_token_patterns() -> None:
    state = EventState()

    blocks = reduce_event(
        {
            "id": "evt_3",
            "type": "agent.tool_use",
            "name": "bash",
            "input": {"command": "echo ghp_abcdefghijklmnopqrstuvwxyz"},
        },
        state,
    )

    assert blocks[0].title.endswith("bash")
    assert "ghp_" not in blocks[0].body


def test_ignores_deltas_and_tolerates_malformed_events() -> None:
    state = EventState()

    assert reduce_event({"type": "event_delta"}, state) == []
    assert reduce_event({}, state)[0].title == "event.malformed"
    assert event_id({"id": 123, "type": "unknown"}) is None


def test_unknown_event_is_visible() -> None:
    blocks = reduce_event(
        {"id": "evt_4", "type": "future.event", "value": 1}, EventState()
    )

    assert blocks[0].title == "future.event"
    assert '"value": 1' in blocks[0].body
