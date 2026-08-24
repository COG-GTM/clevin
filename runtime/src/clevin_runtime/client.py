from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from anthropic.types.beta import BetaManagedAgentsSession
from rich.console import Console
from rich.table import Table

from clevin_runtime.agent_runtime import AgentRuntime, SessionReplay
from clevin_runtime.config import ClientSettings, ConfigurationError
from clevin_runtime.event_view import EventState, reduce_event, render_events
from clevin_runtime.sandbox_runtime import (
    SandboxRuntime,
    SandboxSnapshot,
    validate_session_id,
)

console = Console()


def _resource_value(identifier: str, url: str | None) -> str:
    return f"[link={url}]{identifier}[/link]" if url else identifier


def _sandbox_snapshot(session_id: str) -> SandboxSnapshot:
    try:
        return asyncio.run(SandboxRuntime().snapshot(session_id))
    except Exception:
        return SandboxSnapshot(
            sandbox_id=None,
            status="unavailable",
            volume_path=f"clevin-sessions/sessions/{session_id}",
            sandbox_url=None,
            volume_url=None,
        )


def show_resources(runtime: AgentRuntime, session: BetaManagedAgentsSession) -> None:
    settings = runtime.settings
    modal = _sandbox_snapshot(session.id)
    table = Table(title="Clevin resources", show_header=False)
    table.add_column("Resource", style="bold")
    table.add_column("ID / status")
    table.add_row(
        "Agent",
        _resource_value(
            f"{settings.agent_id} v{settings.agent_version}",
            runtime.platform_url("agents", settings.agent_id),
        ),
    )
    table.add_row(
        "Environment",
        _resource_value(
            settings.environment_id,
            runtime.platform_url("environments", settings.environment_id),
        ),
    )
    table.add_row(
        "Session",
        _resource_value(session.id, runtime.platform_url("sessions", session.id)),
    )
    table.add_row("Session status", session.status)
    table.add_row(
        "Modal Sandbox",
        _resource_value(modal.sandbox_id or modal.status, modal.sandbox_url),
    )
    table.add_row("Modal Volume", _resource_value(modal.volume_path, modal.volume_url))
    console.print(table)


def observe(runtime: AgentRuntime, replay: SessionReplay) -> None:
    state = EventState()
    for persisted_event in replay.events:
        render_events(console, reduce_event(persisted_event, state))

    if not state.terminal:
        try:
            for streamed_event in runtime.stream(
                replay.session.id, replay.seen_event_ids
            ):
                rendered = reduce_event(streamed_event, state)
                render_events(console, rendered)
                if state.terminal:
                    break
        except KeyboardInterrupt:
            console.print(
                "[yellow]Disconnected locally; the remote session was not "
                "interrupted.[/yellow]"
            )

    final_session = runtime.retrieve_session(replay.session.id)
    usage = final_session.usage.model_dump(mode="json")
    budget = (
        final_session.budget.model_dump(mode="json")
        if final_session.budget is not None
        else None
    )
    console.print(f"[bold]Final status:[/bold] {final_session.status}")
    console.print_json(data={"usage": usage, "budget": budget})
    show_resources(runtime, final_session)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start or resume one native Clevin Managed Agents session."
    )
    parser.add_argument(
        "ticket_id", nargs="?", help="Linear ticket ID, for example ENG-123"
    )
    parser.add_argument(
        "--resume", metavar="SESSION_ID", help="Resume and replay a session"
    )
    args = parser.parse_args(argv)
    if (args.ticket_id is None) == (args.resume is None):
        parser.error("provide exactly one Linear ticket ID or --resume SESSION_ID")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    replay: SessionReplay | None = None
    try:
        runtime = AgentRuntime(ClientSettings.from_root_env())
        if args.resume is not None:
            replay = runtime.replay(validate_session_id(args.resume))
        else:
            session = runtime.create_session(args.ticket_id)
            replay = runtime.replay(session.id)
        show_resources(runtime, replay.session)
        observe(runtime, replay)
        return 0
    except (ConfigurationError, ValueError) as error:
        console.print(f"[red]{type(error).__name__}: {error}[/red]")
        return 2
    finally:
        if replay is not None:
            console.print(
                "[dim]Resume later:[/dim] [cyan]uv run --project runtime "
                f"clevin --resume {replay.session.id}[/cyan]"
            )


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
