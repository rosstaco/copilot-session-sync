"""Rich CLI for copilot-session-sync."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .parser import ParsedSession, parse_session_dir
from .scanner import ScanResult, check_docker_available, list_containers, _discover_home_dirs, _run
from .store import DEFAULT_COPILOT_DIR, diff_sessions, get_existing_session_ids, merge_sessions
from .vscode import scan_vscode_workspaces

console = Console()


def _extract_all_sessions(
    staging_dir: Path,
) -> tuple[list[ParsedSession], dict[str, Path], int, int, list[str]]:
    """Extract sessions from all containers, returning sessions, source dirs, and stats."""
    containers = list_containers()
    all_sessions: list[ParsedSession] = []
    source_dirs: dict[str, Path] = {}
    errors: list[str] = []
    containers_with_data = 0

    with console.status("[bold cyan]Scanning containers...") as status:
        for i, container in enumerate(containers):
            status.update(
                f"[bold cyan]Scanning container {i + 1}/{len(containers)}: {container.name}"
            )
            home_dirs = _discover_home_dirs(container.id)
            found_data = False

            for home in home_dirs:
                copilot_path = f"{home}/.copilot/session-state"
                target = staging_dir / f"{container.id}_{home.replace('/', '_')}"

                result = _run(["docker", "cp", f"{container.id}:{copilot_path}", str(target)])
                if result.returncode != 0 or not target.is_dir():
                    continue

                for session_dir in target.iterdir():
                    if not session_dir.is_dir():
                        continue
                    try:
                        parsed = parse_session_dir(session_dir)
                        if parsed:
                            parsed.source_container = container.name
                            parsed.source_path = f"{home}/.copilot"
                            all_sessions.append(parsed)
                            source_dirs[parsed.meta.id] = session_dir
                            found_data = True
                    except Exception as e:
                        errors.append(f"{container.name}:{home}/{session_dir.name}: {e}")

            if found_data:
                containers_with_data += 1

    # Deduplicate
    seen: dict[str, tuple[ParsedSession, Path]] = {}
    for session in all_sessions:
        sid = session.meta.id
        if sid not in seen or len(session.turns) > len(seen[sid][0].turns):
            seen[sid] = (session, source_dirs.get(sid, Path()))

    deduped = [s for s, _ in seen.values()]
    deduped_dirs = {sid: p for sid, (_, p) in seen.items() if p != Path()}

    return deduped, deduped_dirs, len(containers), containers_with_data, errors


def _print_scan_summary(
    new_sessions: list[ParsedSession],
    existing_sessions: list[ParsedSession],
    containers_scanned: int,
    containers_with_data: int,
    errors: list[str],
    *,
    vscode_new: list[ParsedSession] | None = None,
    vscode_existing: list[ParsedSession] | None = None,
    vscode_workspaces: int = 0,
) -> None:
    """Print a rich summary of what was found."""
    console.print()
    if containers_scanned:
        console.print(f"[bold]Scanned {containers_scanned} containers[/bold], "
                      f"[green]{containers_with_data}[/green] had Copilot data")
    if vscode_workspaces:
        vscode_total = len(vscode_new or []) + len(vscode_existing or [])
        console.print(f"[bold]Scanned {vscode_workspaces} VS Code workspaces[/bold], "
                      f"[green]{vscode_total}[/green] chat sessions found")
    console.print()

    all_new = list(new_sessions)
    if vscode_new:
        all_new.extend(vscode_new)

    if not all_new:
        console.print("[yellow]No new sessions to sync.[/yellow] Everything is up to date! ✨")
        return

    # Container sessions table
    if new_sessions:
        table = Table(title=f"🐳 {len(new_sessions)} Container Sessions", show_lines=True)
        table.add_column("Summary", style="bold", max_width=50)
        table.add_column("Working Dir", style="dim", max_width=40)
        table.add_column("Turns", justify="right", style="cyan")
        table.add_column("Container", style="magenta")
        table.add_column("Date", style="dim")

        for session in sorted(new_sessions, key=lambda s: s.meta.created_at or ""):
            summary = session.meta.summary or "[dim]no summary[/dim]"
            cwd = session.meta.cwd or ""
            if cwd.startswith("/workspaces/"):
                cwd = cwd[len("/workspaces/"):]
            turns = str(len(session.turns)) if session.turns else "[dim]0[/dim]"
            date = (session.meta.created_at or "")[:10]
            table.add_row(summary, cwd, turns, session.source_container, date)

        console.print(table)

    # VS Code sessions table
    if vscode_new:
        console.print()
        table = Table(title=f"💬 {len(vscode_new)} VS Code Chat Sessions", show_lines=True)
        table.add_column("Title", style="bold", max_width=50)
        table.add_column("Workspace", style="dim", max_width=40)
        table.add_column("Turns", justify="right", style="cyan")
        table.add_column("Source", style="magenta")
        table.add_column("Date", style="dim")

        for session in sorted(vscode_new, key=lambda s: s.meta.created_at or ""):
            summary = session.meta.summary or "[dim]untitled[/dim]"
            cwd = session.meta.cwd or ""
            turns = str(len(session.turns)) if session.turns else "[dim]0[/dim]"
            source = session.source_container.replace("vscode:", "")
            date = (session.meta.created_at or "")[:10]
            table.add_row(summary, cwd, turns, source, date)

        console.print(table)

    all_existing = len(existing_sessions) + len(vscode_existing or [])
    if all_existing:
        console.print(
            f"\n[dim]{all_existing} sessions already synced (skipped)[/dim]"
        )

    if errors:
        console.print(f"\n[yellow]⚠ {len(errors)} errors during scan:[/yellow]")
        for err in errors[:5]:
            console.print(f"  [dim]{err}[/dim]")
        if len(errors) > 5:
            console.print(f"  [dim]... and {len(errors) - 5} more[/dim]")


def main() -> None:
    """Main entry point for copilot-session-sync."""
    console.print()
    console.print("[bold cyan]🔄 copilot-session-sync[/bold cyan]")
    console.print("[dim]Consolidate Copilot sessions from devcontainers and VS Code to your host[/dim]")
    console.print()

    copilot_dir = DEFAULT_COPILOT_DIR
    db_path = copilot_dir / "session-store.db"
    if not db_path.exists():
        console.print(f"[bold red]Error:[/bold red] No session-store.db found at {db_path}")
        console.print("Run Copilot CLI at least once on this host first.")
        sys.exit(1)

    existing_ids = get_existing_session_ids(db_path)

    # Phase 1: Scan Docker containers
    docker_available = check_docker_available()
    container_new: list[ParsedSession] = []
    container_existing: list[ParsedSession] = []
    source_dirs: dict[str, Path] = {}
    total_containers = 0
    containers_with_data = 0
    errors: list[str] = []

    tmpdir_obj = tempfile.TemporaryDirectory(prefix="copilot-sync-")
    staging = Path(tmpdir_obj.name)

    if docker_available:
        sessions, source_dirs, total_containers, containers_with_data, errors = (
            _extract_all_sessions(staging)
        )
        container_new, container_existing = diff_sessions(sessions, existing_ids)
    else:
        console.print("[dim]Docker not available, skipping container scan.[/dim]")

    # Phase 2: Scan VS Code workspaces
    vscode_new: list[ParsedSession] = []
    vscode_existing: list[ParsedSession] = []
    vscode_workspace_count = 0

    with console.status("[bold cyan]Scanning VS Code workspaces..."):
        workspaces = scan_vscode_workspaces()
        vscode_workspace_count = len(workspaces)
        all_vscode_sessions = [s for ws in workspaces for s in ws.sessions]
        vscode_new, vscode_existing = diff_sessions(all_vscode_sessions, existing_ids)

    # Present combined results
    _print_scan_summary(
        container_new, container_existing,
        total_containers, containers_with_data, errors,
        vscode_new=vscode_new,
        vscode_existing=vscode_existing,
        vscode_workspaces=vscode_workspace_count,
    )

    all_new = container_new + vscode_new
    if not all_new:
        tmpdir_obj.cleanup()
        sys.exit(0)

    # Confirm
    console.print()
    total_turns = sum(len(s.turns) for s in all_new)
    console.print(
        f"Will import [bold green]{len(all_new)}[/bold green] sessions "
        f"([cyan]{total_turns}[/cyan] conversation turns) into host session store."
    )
    if not Confirm.ask("\n[bold]Proceed with sync?[/bold]", default=True):
        console.print("[dim]Cancelled.[/dim]")
        tmpdir_obj.cleanup()
        sys.exit(0)

    # Sync
    console.print()
    with console.status("[bold cyan]Syncing sessions..."):
        stats = merge_sessions(
            all_new, copilot_dir=copilot_dir, source_dirs=source_dirs
        )

    tmpdir_obj.cleanup()

    # Report
    console.print()
    console.print("[bold green]✅ Sync complete![/bold green]")
    console.print(f"  Sessions imported: [bold]{stats.sessions_imported}[/bold]")
    console.print(f"  Turns indexed:     [bold]{stats.turns_imported}[/bold]")
    if stats.sessions_skipped:
        console.print(f"  Skipped (dupes):   [dim]{stats.sessions_skipped}[/dim]")
    if stats.backup_path:
        console.print(f"  Backup:            [dim]{stats.backup_path}[/dim]")
    console.print()
