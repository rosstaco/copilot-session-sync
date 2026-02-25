"""Scan Docker containers for Copilot CLI session data."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .parser import ParsedSession, parse_session_dir

# Common home directories in devcontainers
DEFAULT_HOME_DIRS = [
    "/home/vscode",
    "/home/node",
    "/home/codespace",
    "/root",
]


@dataclass
class ContainerInfo:
    """Metadata about a Docker container."""

    id: str
    name: str
    status: str


@dataclass
class ScanResult:
    """Result of scanning all containers."""

    sessions: list[ParsedSession]
    containers_scanned: int
    containers_with_data: int
    errors: list[str]


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def check_docker_available() -> bool:
    """Check if Docker CLI is available and the daemon is running."""
    try:
        result = _run(["docker", "info"])
        return result.returncode == 0
    except FileNotFoundError:
        return False


def list_containers() -> list[ContainerInfo]:
    """List all Docker containers (running and stopped)."""
    result = _run(
        ["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
        check=True,
    )
    containers = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            containers.append(ContainerInfo(id=parts[0], name=parts[1], status=parts[2]))
    return containers


def _discover_home_dirs(container_id: str) -> list[str]:
    """Discover user home directories from a container's /etc/passwd."""
    homes = list(DEFAULT_HOME_DIRS)
    result = _run(["docker", "cp", f"{container_id}:/etc/passwd", "-"])
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 6:
                home = parts[5]
                uid_str = parts[2]
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                # Include non-system users with real home dirs
                if uid >= 1000 and home and home not in homes and home != "/nonexistent":
                    homes.append(home)
    return homes


def _extract_sessions_from_container(
    container: ContainerInfo, staging_dir: Path
) -> tuple[list[ParsedSession], list[str]]:
    """Extract session-state directories from a container."""
    sessions: list[ParsedSession] = []
    errors: list[str] = []
    home_dirs = _discover_home_dirs(container.id)

    for home in home_dirs:
        copilot_path = f"{home}/.copilot/session-state"
        target = staging_dir / f"{container.id}_{home.replace('/', '_')}"

        result = _run(["docker", "cp", f"{container.id}:{copilot_path}", str(target)])
        if result.returncode != 0:
            continue

        if not target.is_dir():
            continue

        for session_dir in target.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                parsed = parse_session_dir(session_dir)
                if parsed:
                    parsed.source_container = container.name
                    parsed.source_path = f"{home}/.copilot"
                    sessions.append(parsed)
            except Exception as e:
                errors.append(f"{container.name}:{home}/{session_dir.name}: {e}")

    return sessions, errors


def scan_containers(
    *, progress_callback: callable | None = None,
) -> ScanResult:
    """Scan all Docker containers for Copilot session data.

    Args:
        progress_callback: Optional callback(container_name, index, total) for progress updates.
    """
    containers = list_containers()
    all_sessions: list[ParsedSession] = []
    all_errors: list[str] = []
    containers_with_data = 0

    with tempfile.TemporaryDirectory(prefix="copilot-sync-") as tmpdir:
        staging = Path(tmpdir)
        for i, container in enumerate(containers):
            if progress_callback:
                progress_callback(container.name, i, len(containers))

            sessions, errors = _extract_sessions_from_container(container, staging)
            if sessions:
                containers_with_data += 1
            all_sessions.extend(sessions)
            all_errors.extend(errors)

    # Deduplicate by session ID (same session may appear in multiple containers)
    seen: dict[str, ParsedSession] = {}
    for session in all_sessions:
        sid = session.meta.id
        if sid not in seen or len(session.turns) > len(seen[sid].turns):
            seen[sid] = session  # keep the one with more data
    deduped = list(seen.values())

    return ScanResult(
        sessions=deduped,
        containers_scanned=len(containers),
        containers_with_data=containers_with_data,
        errors=all_errors,
    )
