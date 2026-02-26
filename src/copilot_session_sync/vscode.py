"""Parse VS Code Copilot Chat session data from workspaceStorage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .parser import ParsedSession, SessionMeta, Turn

# VS Code stores workspace data here on macOS
VSCODE_STORAGE_PATHS = [
    Path.home() / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage",
]


@dataclass
class VSCodeWorkspace:
    """A VS Code workspace with its chat sessions."""

    workspace_id: str
    workspace_path: str
    workspace_name: str
    is_remote: bool
    sessions: list[ParsedSession] = field(default_factory=list)


def _resolve_workspace_name(workspace_json: Path) -> tuple[str, bool]:
    """Extract workspace name and whether it's a devcontainer from workspace.json."""
    try:
        d = json.loads(workspace_json.read_text())
        raw = d.get("folder", "") or d.get("workspace", "")
        decoded = unquote(raw)
        is_remote = "dev-container" in raw or "vscode-remote" in raw

        # Simplify path to something readable
        name = decoded
        if "/workspaces/" in name:
            name = name.split("/workspaces/")[-1]
        elif "repos/" in name:
            name = "repos/" + name.split("repos/")[-1]
        elif name.startswith("file:///Users/"):
            name = name.replace("file:///Users/", "~/")
            parts = name.split("/")
            if len(parts) > 3:
                name = "/".join(parts[-2:])

        return name, is_remote
    except (json.JSONDecodeError, OSError):
        return "unknown", False


def _get_session_index(state_db: Path) -> dict[str, dict]:
    """Read the chat session index from state.vscdb."""
    try:
        conn = sqlite3.connect(str(state_db))
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'chat.ChatSessionStore.index'"
        ).fetchone()
        conn.close()
        if row:
            index = json.loads(row[0])
            return index.get("entries", {})
    except (sqlite3.Error, json.JSONDecodeError):
        pass
    return {}


def _parse_chat_json(path: Path, index_entry: dict | None = None) -> ParsedSession | None:
    """Parse a VS Code chat session JSON file into a ParsedSession."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    requests = data.get("requests", [])
    if not requests:
        return None

    session_id = path.stem  # filename without extension
    location = data.get("initialLocation", "panel")

    # Extract title and timestamps from index if available
    title = None
    created_at = None
    updated_at = None
    if index_entry:
        title = index_entry.get("title")
        timing = index_entry.get("timing", {})
        created_ts = timing.get("created")
        last_ts = timing.get("lastRequestEnded") or timing.get("lastRequestStarted")
        if created_ts:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc).isoformat()
        if last_ts:
            updated_at = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat()

    turns: list[Turn] = []
    for i, req in enumerate(requests):
        # Extract user message
        msg = req.get("message", {})
        user_text = ""
        if isinstance(msg, dict):
            user_text = msg.get("text", "")
        elif isinstance(msg, str):
            user_text = msg

        if not user_text:
            continue

        # Extract assistant response
        resp = req.get("response", [])
        resp_text = ""
        if isinstance(resp, list):
            for part in resp:
                if isinstance(part, dict):
                    val = part.get("value", "")
                    if isinstance(val, str):
                        resp_text += val
        elif isinstance(resp, str):
            resp_text = resp

        turns.append(
            Turn(
                turn_index=i,
                user_message=user_text,
                assistant_response=resp_text.strip(),
            )
        )

    if not turns:
        return None

    meta = SessionMeta(
        id=f"vscode-{session_id}",
        summary=title if title and title != "New Chat" else None,
        created_at=created_at,
        updated_at=updated_at,
    )

    return ParsedSession(meta=meta, turns=turns)


def scan_vscode_workspaces() -> list[VSCodeWorkspace]:
    """Scan all VS Code workspaces for Copilot Chat sessions."""
    workspaces: list[VSCodeWorkspace] = []

    for storage_base in VSCODE_STORAGE_PATHS:
        if not storage_base.exists():
            continue

        for ws_dir in sorted(storage_base.iterdir()):
            if not ws_dir.is_dir():
                continue

            chat_dir = ws_dir / "chatSessions"
            if not chat_dir.exists():
                continue

            # Get workspace info
            workspace_json = ws_dir / "workspace.json"
            name, is_remote = _resolve_workspace_name(workspace_json)

            # Get session index from state.vscdb
            state_db = ws_dir / "state.vscdb"
            session_index = _get_session_index(state_db) if state_db.exists() else {}

            # Parse all chat session files
            sessions: list[ParsedSession] = []
            for chat_file in sorted(chat_dir.iterdir()):
                if not chat_file.suffix == ".json":
                    # Also handle .jsonl files (older format)
                    if chat_file.suffix == ".jsonl":
                        session = _parse_chat_jsonl(chat_file, session_index)
                        if session:
                            session.source_container = f"vscode:{name}"
                            session.source_path = str(ws_dir)
                            # Set cwd from workspace
                            if not session.meta.cwd:
                                session.meta.cwd = name
                            sessions.append(session)
                    continue

                # Look up index entry by session ID (filename)
                index_entry = session_index.get(chat_file.stem)

                session = _parse_chat_json(chat_file, index_entry)
                if session:
                    session.source_container = f"vscode:{name}"
                    session.source_path = str(ws_dir)
                    if not session.meta.cwd:
                        session.meta.cwd = name
                    sessions.append(session)

            if sessions:
                workspaces.append(
                    VSCodeWorkspace(
                        workspace_id=ws_dir.name,
                        workspace_path=name,
                        workspace_name=name,
                        is_remote=is_remote,
                        sessions=sessions,
                    )
                )

    return workspaces


def _parse_chat_jsonl(path: Path, session_index: dict) -> ParsedSession | None:
    """Parse a VS Code chat session JSONL file (incremental format)."""
    try:
        lines = path.read_text().strip().splitlines()
    except OSError:
        return None

    if not lines:
        return None

    session_id = path.stem
    init_data = None
    requests_data: list[dict] = []

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = entry.get("kind")
        if kind == 0:
            init_data = entry.get("v", {})
        elif kind == 2:
            v = entry.get("v", [])
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and "message" in item:
                        requests_data.append(item)

    if not requests_data:
        return None

    # Look up index for metadata
    index_entry = session_index.get(session_id)
    title = None
    created_at = None
    updated_at = None
    if index_entry:
        title = index_entry.get("title")
        timing = index_entry.get("timing", {})
        created_ts = timing.get("created")
        last_ts = timing.get("lastRequestEnded")
        if created_ts:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc).isoformat()
        if last_ts:
            updated_at = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat()

    turns: list[Turn] = []
    for i, req in enumerate(requests_data):
        msg = req.get("message", {})
        user_text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
        if not user_text:
            continue

        resp = req.get("response", [])
        resp_text = ""
        if isinstance(resp, list):
            for part in resp:
                if isinstance(part, dict):
                    resp_text += part.get("value", "")

        turns.append(
            Turn(turn_index=i, user_message=user_text, assistant_response=resp_text.strip())
        )

    if not turns:
        return None

    meta = SessionMeta(
        id=f"vscode-{session_id}",
        summary=title if title and title != "New Chat" else None,
        created_at=created_at,
        updated_at=updated_at,
    )

    return ParsedSession(meta=meta, turns=turns)
