"""Parse Copilot CLI session data from workspace.yaml and events.jsonl files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionMeta:
    """Metadata parsed from workspace.yaml."""

    id: str
    cwd: str | None = None
    git_root: str | None = None
    repository: str | None = None
    branch: str | None = None
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class Turn:
    """A single user/assistant conversation turn."""

    turn_index: int
    user_message: str
    assistant_response: str
    timestamp: str = ""


@dataclass
class ParsedSession:
    """Fully parsed session with metadata and conversation turns."""

    meta: SessionMeta
    turns: list[Turn] = field(default_factory=list)
    source_container: str = ""
    source_path: str = ""


def parse_workspace_yaml(path: Path) -> SessionMeta:
    """Parse workspace.yaml into SessionMeta.

    The format is simple key: value pairs, one per line.
    """
    data: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip()

    return SessionMeta(
        id=data.get("id", path.parent.name),
        cwd=data.get("cwd") or None,
        git_root=data.get("git_root") or None,
        repository=data.get("repository") or None,
        branch=data.get("branch") or None,
        summary=data.get("summary") or None,
        created_at=data.get("created_at") or None,
        updated_at=data.get("updated_at") or None,
    )


def parse_events_jsonl(path: Path) -> tuple[list[Turn], SessionMeta | None]:
    """Parse events.jsonl into turns and optional enriched metadata.

    Returns a tuple of (turns, enriched_meta) where enriched_meta contains
    any extra fields from the session.start event (repository, branch, etc).
    """
    turns: list[Turn] = []
    enriched_meta: SessionMeta | None = None
    current_user_msg: str | None = None
    current_assistant_msg = ""
    turn_index = 0
    turn_timestamp = ""

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "session.start":
                ctx = event.get("data", {}).get("context", {})
                enriched_meta = SessionMeta(
                    id=event.get("data", {}).get("sessionId", ""),
                    cwd=ctx.get("cwd"),
                    git_root=ctx.get("gitRoot"),
                    repository=ctx.get("repository"),
                    branch=ctx.get("branch"),
                )

            elif event_type == "user.message":
                if current_user_msg is not None:
                    turns.append(
                        Turn(
                            turn_index=turn_index,
                            user_message=current_user_msg,
                            assistant_response=current_assistant_msg.strip(),
                            timestamp=turn_timestamp,
                        )
                    )
                    turn_index += 1
                    current_assistant_msg = ""

                current_user_msg = event.get("data", {}).get("content", "")
                turn_timestamp = event.get("timestamp", "")

            elif event_type == "assistant.message":
                content = event.get("data", {}).get("content", "")
                current_assistant_msg += content

    # Flush last turn
    if current_user_msg is not None:
        turns.append(
            Turn(
                turn_index=turn_index,
                user_message=current_user_msg,
                assistant_response=current_assistant_msg.strip(),
                timestamp=turn_timestamp,
            )
        )

    return turns, enriched_meta


def parse_session_dir(session_dir: Path) -> ParsedSession | None:
    """Parse a complete session directory into a ParsedSession."""
    workspace_yaml = session_dir / "workspace.yaml"
    if not workspace_yaml.exists():
        return None

    meta = parse_workspace_yaml(workspace_yaml)

    events_path = session_dir / "events.jsonl"
    turns: list[Turn] = []
    if events_path.exists():
        turns, enriched = parse_events_jsonl(events_path)
        # Enrich metadata from session.start event
        if enriched:
            if not meta.repository and enriched.repository:
                meta.repository = enriched.repository
            if not meta.branch and enriched.branch:
                meta.branch = enriched.branch
            if not meta.git_root and enriched.git_root:
                meta.git_root = enriched.git_root
            if not meta.cwd and enriched.cwd:
                meta.cwd = enriched.cwd

    return ParsedSession(meta=meta, turns=turns)
