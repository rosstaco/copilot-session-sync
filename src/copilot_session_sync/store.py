"""Merge extracted sessions into the host's session-store.db."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .parser import ParsedSession

DEFAULT_COPILOT_DIR = Path.home() / ".copilot"


@dataclass
class MergeStats:
    """Statistics from a merge operation."""

    sessions_imported: int = 0
    sessions_skipped: int = 0
    turns_imported: int = 0
    backup_path: Path | None = None


def get_existing_session_ids(db_path: Path) -> set[str]:
    """Get the set of session IDs already in the store."""
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT id FROM sessions")
        return {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()


def diff_sessions(
    extracted: list[ParsedSession], existing_ids: set[str]
) -> tuple[list[ParsedSession], list[ParsedSession]]:
    """Split extracted sessions into new and existing."""
    new = []
    existing = []
    for session in extracted:
        if session.meta.id in existing_ids:
            existing.append(session)
        else:
            new.append(session)
    return new, existing


def backup_store(db_path: Path) -> Path:
    """Create a backup of session-store.db, returns the backup path."""
    backup = db_path.with_suffix(".db.bak")
    shutil.copy2(db_path, backup)
    return backup


def _copy_session_state(session: ParsedSession, staging_dir: Path, target_dir: Path) -> bool:
    """Copy a session's state directory from staging to the host."""
    sid = session.meta.id
    dest = target_dir / sid
    if dest.exists():
        return False

    # Find the session dir in the staging area
    # The scanner extracts to staging_dir/container_path/session-state/session_id/
    # But by the time we get here, ParsedSession doesn't carry the staging path.
    # Instead, we re-extract using docker cp in the CLI flow.
    # This function handles the case where files are already in a staging area.
    return False


def merge_sessions(
    sessions: list[ParsedSession],
    *,
    copilot_dir: Path = DEFAULT_COPILOT_DIR,
    source_dirs: dict[str, Path] | None = None,
) -> MergeStats:
    """Merge new sessions into the host's session-store.db.

    Args:
        sessions: List of new sessions to import.
        copilot_dir: Path to the host's ~/.copilot directory.
        source_dirs: Mapping of session_id → source directory Path for copying session-state.
    """
    db_path = copilot_dir / "session-store.db"
    session_state_dir = copilot_dir / "session-state"
    stats = MergeStats()

    # Backup
    if db_path.exists():
        stats.backup_path = backup_store(db_path)

    session_state_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        for session in sessions:
            meta = session.meta
            sid = meta.id

            # Copy session-state directory if source provided
            if source_dirs and sid in source_dirs:
                dest = session_state_dir / sid
                if not dest.exists():
                    shutil.copytree(source_dirs[sid], dest)

            # Insert session metadata
            try:
                conn.execute(
                    "INSERT INTO sessions (id, cwd, repository, branch, summary, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid,
                        meta.cwd,
                        meta.repository,
                        meta.branch,
                        meta.summary,
                        meta.created_at,
                        meta.updated_at or meta.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                stats.sessions_skipped += 1
                continue

            # Insert turns
            for turn in session.turns:
                try:
                    conn.execute(
                        "INSERT INTO turns (session_id, turn_index, user_message, assistant_response, timestamp) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sid, turn.turn_index, turn.user_message, turn.assistant_response, turn.timestamp),
                    )
                    stats.turns_imported += 1
                except sqlite3.IntegrityError:
                    pass

                # FTS5 search index
                if turn.user_message:
                    conn.execute(
                        "INSERT INTO search_index (content, session_id, source_type, source_id) "
                        "VALUES (?, ?, ?, ?)",
                        (turn.user_message, sid, "turn", str(turn.turn_index)),
                    )
                if turn.assistant_response:
                    conn.execute(
                        "INSERT INTO search_index (content, session_id, source_type, source_id) "
                        "VALUES (?, ?, ?, ?)",
                        (turn.assistant_response, sid, "turn", str(turn.turn_index)),
                    )

            stats.sessions_imported += 1

        conn.commit()
    finally:
        conn.close()

    return stats
