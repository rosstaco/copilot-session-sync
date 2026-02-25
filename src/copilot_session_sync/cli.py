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
) -> None:
    """Print a rich summary of what was found."""
    console.print()
    console.print(f"[bold]Scanned {containers_scanned} containers[/bold], "
                  f"[green]{containers_with_data}[/green] had Copilot data")
    console.print()

    if not new_sessions:
        console.print("[yellow]No new sessions to sync.[/yellow] Everything is up to date! ✨")
        return

    table = Table(title=f"🆕 {len(new_sessions)} New Sessions to Sync", show_lines=True)
    table.add_column("Summary", style="bold", max_width=50)
    table.add_column("Working Dir", style="dim", max_width=40)
    table.add_column("Turns", justify="right", style="cyan")
    table.add_column("Container", style="magenta")
    table.add_column("Date", style="dim")

    for session in sorted(new_sessions, key=lambda s: s.meta.created_at or ""):
        summary = session.meta.summary or "[dim]no summary[/dim]"
        cwd = session.meta.cwd or ""
        # Shorten container paths
        if cwd.startswith("/workspaces/"):
            cwd = cwd[len("/workspaces/"):]
        turns = str(len(session.turns)) if session.turns else "[dim]0[/dim]"
        date = (session.meta.created_at or "")[:10]
        table.add_row(summary, cwd, turns, session.source_container, date)

    console.print(table)

    if existing_sessions:
        console.print(
            f"\n[dim]{len(existing_sessions)} sessions already synced (skipped)[/dim]"
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
    console.print("[dim]Consolidate Copilot CLI sessions from devcontainers to your host[/dim]")
    console.print()

    # Preflight checks
    if not check_docker_available():
        console.print("[bold red]Error:[/bold red] Docker is not running or not installed.")
        sys.exit(1)

    copilot_dir = DEFAULT_COPILOT_DIR
    db_path = copilot_dir / "session-store.db"
    if not db_path.exists():
        console.print(f"[bold red]Error:[/bold red] No session-store.db found at {db_path}")
        console.print("Run Copilot CLI at least once on this host first.")
        sys.exit(1)

    # Scan
    with tempfile.TemporaryDirectory(prefix="copilot-sync-") as tmpdir:
        staging = Path(tmpdir)
        sessions, source_dirs, total_containers, containers_with_data, errors = (
            _extract_all_sessions(staging)
        )

        if not sessions:
            console.print("[yellow]No Copilot sessions found in any container.[/yellow]")
            sys.exit(0)

        # Diff against existing
        existing_ids = get_existing_session_ids(db_path)
        new_sessions, existing_sessions = diff_sessions(sessions, existing_ids)

        # Present
        _print_scan_summary(
            new_sessions, existing_sessions, total_containers, containers_with_data, errors
        )

        if not new_sessions:
            sys.exit(0)

        # Confirm
        console.print()
        total_turns = sum(len(s.turns) for s in new_sessions)
        console.print(
            f"Will import [bold green]{len(new_sessions)}[/bold green] sessions "
            f"([cyan]{total_turns}[/cyan] conversation turns) into host session store."
        )
        if not Confirm.ask("\n[bold]Proceed with sync?[/bold]", default=True):
            console.print("[dim]Cancelled.[/dim]")
            sys.exit(0)

        # Sync
        console.print()
        with console.status("[bold cyan]Syncing sessions...") as status:
            stats = merge_sessions(
                new_sessions, copilot_dir=copilot_dir, source_dirs=source_dirs
            )

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
