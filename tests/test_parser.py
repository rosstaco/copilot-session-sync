"""Tests for the parser module."""

from pathlib import Path
from textwrap import dedent

from copilot_session_sync.parser import (
    ParsedSession,
    SessionMeta,
    Turn,
    parse_events_jsonl,
    parse_session_dir,
    parse_workspace_yaml,
)


def test_parse_workspace_yaml_full(tmp_path: Path) -> None:
    yaml_file = tmp_path / "workspace.yaml"
    yaml_file.write_text(dedent("""\
        id: abc-123
        cwd: /workspaces/myproject
        git_root: /workspaces/myproject
        repository: user/myproject
        branch: main
        summary: Fixed login bug
        summary_count: 0
        created_at: 2026-02-12T07:04:48.303Z
        updated_at: 2026-02-12T07:05:38.408Z
    """))

    meta = parse_workspace_yaml(yaml_file)
    assert meta.id == "abc-123"
    assert meta.cwd == "/workspaces/myproject"
    assert meta.repository == "user/myproject"
    assert meta.branch == "main"
    assert meta.summary == "Fixed login bug"
    assert meta.created_at == "2026-02-12T07:04:48.303Z"
    assert meta.updated_at == "2026-02-12T07:05:38.408Z"


def test_parse_workspace_yaml_minimal(tmp_path: Path) -> None:
    yaml_file = tmp_path / "workspace.yaml"
    yaml_file.write_text(dedent("""\
        id: minimal-session
        cwd: /home/user
        summary_count: 0
        created_at: 2026-01-01T00:00:00.000Z
        updated_at: 2026-01-01T00:00:00.000Z
    """))

    meta = parse_workspace_yaml(yaml_file)
    assert meta.id == "minimal-session"
    assert meta.cwd == "/home/user"
    assert meta.repository is None
    assert meta.branch is None
    assert meta.summary is None


def test_parse_events_jsonl_with_turns(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        '{"type":"session.start","data":{"sessionId":"s1","context":{"cwd":"/workspaces/proj","repository":"user/proj","branch":"dev"}},"timestamp":"2026-02-12T07:05:03.925Z"}\n'
        '{"type":"user.message","data":{"content":"Hello world"},"timestamp":"2026-02-12T07:05:34.940Z"}\n'
        '{"type":"assistant.turn_start","data":{"turnId":"0"},"timestamp":"2026-02-12T07:05:35.000Z"}\n'
        '{"type":"assistant.message","data":{"content":"Hi there!"},"timestamp":"2026-02-12T07:05:38.303Z"}\n'
        '{"type":"user.message","data":{"content":"What is 2+2?"},"timestamp":"2026-02-12T07:06:00.000Z"}\n'
        '{"type":"assistant.message","data":{"content":"The answer"},"timestamp":"2026-02-12T07:06:05.000Z"}\n'
        '{"type":"assistant.message","data":{"content":" is 4."},"timestamp":"2026-02-12T07:06:06.000Z"}\n'
    )

    turns, enriched = parse_events_jsonl(events_file)

    assert len(turns) == 2
    assert turns[0].turn_index == 0
    assert turns[0].user_message == "Hello world"
    assert turns[0].assistant_response == "Hi there!"
    assert turns[1].turn_index == 1
    assert turns[1].user_message == "What is 2+2?"
    assert turns[1].assistant_response == "The answer is 4."

    assert enriched is not None
    assert enriched.repository == "user/proj"
    assert enriched.branch == "dev"


def test_parse_events_jsonl_empty(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("")

    turns, enriched = parse_events_jsonl(events_file)
    assert turns == []
    assert enriched is None


def test_parse_events_jsonl_malformed_lines(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "not valid json\n"
        '{"type":"user.message","data":{"content":"Valid msg"},"timestamp":"2026-01-01T00:00:00Z"}\n'
        '{"type":"assistant.message","data":{"content":"Reply"},"timestamp":"2026-01-01T00:00:01Z"}\n'
    )

    turns, _ = parse_events_jsonl(events_file)
    assert len(turns) == 1
    assert turns[0].user_message == "Valid msg"


def test_parse_session_dir_full(tmp_path: Path) -> None:
    session_dir = tmp_path / "abc-123"
    session_dir.mkdir()
    (session_dir / "workspace.yaml").write_text(dedent("""\
        id: abc-123
        cwd: /workspaces/proj
        summary_count: 0
        created_at: 2026-02-12T07:04:48.303Z
        updated_at: 2026-02-12T07:05:38.408Z
    """))
    (session_dir / "events.jsonl").write_text(
        '{"type":"session.start","data":{"sessionId":"abc-123","context":{"cwd":"/workspaces/proj","repository":"user/proj","branch":"main"}},"timestamp":"2026-02-12T07:05:03.925Z"}\n'
        '{"type":"user.message","data":{"content":"Test"},"timestamp":"2026-02-12T07:05:34.940Z"}\n'
        '{"type":"assistant.message","data":{"content":"Response"},"timestamp":"2026-02-12T07:05:38.303Z"}\n'
    )

    result = parse_session_dir(session_dir)
    assert result is not None
    assert result.meta.id == "abc-123"
    assert result.meta.repository == "user/proj"  # enriched from events
    assert len(result.turns) == 1


def test_parse_session_dir_no_workspace_yaml(tmp_path: Path) -> None:
    session_dir = tmp_path / "no-yaml"
    session_dir.mkdir()

    result = parse_session_dir(session_dir)
    assert result is None


def test_parse_session_dir_metadata_only(tmp_path: Path) -> None:
    session_dir = tmp_path / "metadata-only"
    session_dir.mkdir()
    (session_dir / "workspace.yaml").write_text(dedent("""\
        id: metadata-only
        cwd: /workspaces/proj
        summary: Just metadata
        created_at: 2026-01-01T00:00:00Z
        updated_at: 2026-01-01T00:00:00Z
    """))

    result = parse_session_dir(session_dir)
    assert result is not None
    assert result.meta.summary == "Just metadata"
    assert result.turns == []
