"""Tests for the store module."""

import sqlite3
from pathlib import Path

import pytest

from copilot_session_sync.parser import ParsedSession, SessionMeta, Turn
from copilot_session_sync.store import diff_sessions, get_existing_session_ids, merge_sessions


def _create_store_db(db_path: Path) -> None:
    """Create a minimal session-store.db with the required schema."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            cwd TEXT,
            repository TEXT,
            branch TEXT,
            summary TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            turn_index INTEGER NOT NULL,
            user_message TEXT,
            assistant_response TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            UNIQUE(session_id, turn_index)
        );
        CREATE VIRTUAL TABLE search_index USING fts5(
            content,
            session_id UNINDEXED,
            source_type UNINDEXED,
            source_id UNINDEXED
        );
    """)
    conn.close()


def _make_session(sid: str, summary: str = "", turns: int = 0) -> ParsedSession:
    turn_list = [
        Turn(turn_index=i, user_message=f"msg {i}", assistant_response=f"resp {i}")
        for i in range(turns)
    ]
    return ParsedSession(
        meta=SessionMeta(
            id=sid,
            cwd="/workspaces/test",
            summary=summary or None,
            created_at="2026-01-01T00:00:00Z",
        ),
        turns=turn_list,
    )


class TestDiffSessions:
    def test_all_new(self) -> None:
        sessions = [_make_session("s1"), _make_session("s2")]
        new, existing = diff_sessions(sessions, set())
        assert len(new) == 2
        assert len(existing) == 0

    def test_all_existing(self) -> None:
        sessions = [_make_session("s1"), _make_session("s2")]
        new, existing = diff_sessions(sessions, {"s1", "s2"})
        assert len(new) == 0
        assert len(existing) == 2

    def test_mixed(self) -> None:
        sessions = [_make_session("s1"), _make_session("s2"), _make_session("s3")]
        new, existing = diff_sessions(sessions, {"s2"})
        assert len(new) == 2
        assert len(existing) == 1
        assert {s.meta.id for s in new} == {"s1", "s3"}


class TestGetExistingSessionIds:
    def test_no_db(self, tmp_path: Path) -> None:
        ids = get_existing_session_ids(tmp_path / "nonexistent.db")
        assert ids == set()

    def test_with_sessions(self, tmp_path: Path) -> None:
        db_path = tmp_path / "store.db"
        _create_store_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO sessions (id) VALUES ('existing-1')")
        conn.execute("INSERT INTO sessions (id) VALUES ('existing-2')")
        conn.commit()
        conn.close()

        ids = get_existing_session_ids(db_path)
        assert ids == {"existing-1", "existing-2"}


class TestMergeSessions:
    def test_import_sessions_with_turns(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / ".copilot"
        copilot_dir.mkdir()
        db_path = copilot_dir / "session-store.db"
        _create_store_db(db_path)

        sessions = [_make_session("s1", summary="Test session", turns=3)]
        stats = merge_sessions(sessions, copilot_dir=copilot_dir)

        assert stats.sessions_imported == 1
        assert stats.turns_imported == 3
        assert stats.sessions_skipped == 0

        # Verify DB contents
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT id, summary FROM sessions").fetchall()
        assert len(rows) == 1
        assert rows[0] == ("s1", "Test session")

        turns = conn.execute("SELECT turn_index, user_message FROM turns WHERE session_id = 's1'").fetchall()
        assert len(turns) == 3

        # Verify FTS5 indexing
        fts = conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index MATCH 'msg'").fetchone()
        assert fts[0] > 0
        conn.close()

    def test_skip_duplicates(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / ".copilot"
        copilot_dir.mkdir()
        db_path = copilot_dir / "session-store.db"
        _create_store_db(db_path)

        # Pre-insert a session
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO sessions (id) VALUES ('existing')")
        conn.commit()
        conn.close()

        sessions = [_make_session("existing", turns=1)]
        stats = merge_sessions(sessions, copilot_dir=copilot_dir)

        assert stats.sessions_imported == 0
        assert stats.sessions_skipped == 1

    def test_backup_created(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / ".copilot"
        copilot_dir.mkdir()
        db_path = copilot_dir / "session-store.db"
        _create_store_db(db_path)

        stats = merge_sessions([], copilot_dir=copilot_dir)
        assert stats.backup_path is not None
        assert stats.backup_path.exists()

    def test_copies_session_state_dirs(self, tmp_path: Path) -> None:
        copilot_dir = tmp_path / ".copilot"
        copilot_dir.mkdir()
        db_path = copilot_dir / "session-store.db"
        _create_store_db(db_path)

        # Create a source session dir
        source = tmp_path / "staging" / "s1"
        source.mkdir(parents=True)
        (source / "workspace.yaml").write_text("id: s1\ncwd: /test\n")
        (source / "events.jsonl").write_text("")

        sessions = [_make_session("s1")]
        stats = merge_sessions(sessions, copilot_dir=copilot_dir, source_dirs={"s1": source})

        assert stats.sessions_imported == 1
        dest = copilot_dir / "session-state" / "s1"
        assert dest.exists()
        assert (dest / "workspace.yaml").exists()
