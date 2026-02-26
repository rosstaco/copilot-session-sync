"""Tests for the vscode parser module."""

import json
from pathlib import Path

from copilot_session_sync.vscode import _parse_chat_json, _parse_chat_jsonl


def test_parse_chat_json_basic(tmp_path: Path) -> None:
    session_file = tmp_path / "abc-123.json"
    session_file.write_text(json.dumps({
        "version": 3,
        "initialLocation": "panel",
        "requests": [
            {
                "message": {"text": "How do I sort a list?"},
                "response": [{"value": "Use the `sorted()` function."}],
                "modelId": "copilot/gpt-4o",
            },
            {
                "message": {"text": "Show me an example"},
                "response": [
                    {"value": "Here's an example:\n"},
                    {"value": "```python\nsorted([3,1,2])\n```"},
                ],
            },
        ],
    }))

    result = _parse_chat_json(session_file)
    assert result is not None
    assert result.meta.id == "vscode-abc-123"
    assert len(result.turns) == 2
    assert result.turns[0].user_message == "How do I sort a list?"
    assert result.turns[0].assistant_response == "Use the `sorted()` function."
    assert "```python" in result.turns[1].assistant_response


def test_parse_chat_json_with_index(tmp_path: Path) -> None:
    session_file = tmp_path / "sess-456.json"
    session_file.write_text(json.dumps({
        "version": 3,
        "requests": [
            {"message": {"text": "Hello"}, "response": [{"value": "Hi!"}]},
        ],
    }))

    index_entry = {
        "title": "Greeting chat",
        "timing": {"created": 1770688423673, "lastRequestEnded": 1770690090370},
    }

    result = _parse_chat_json(session_file, index_entry)
    assert result is not None
    assert result.meta.summary == "Greeting chat"
    assert result.meta.created_at is not None
    assert "2026" in result.meta.created_at


def test_parse_chat_json_empty_requests(tmp_path: Path) -> None:
    session_file = tmp_path / "empty.json"
    session_file.write_text(json.dumps({"version": 3, "requests": []}))

    result = _parse_chat_json(session_file)
    assert result is None


def test_parse_chat_json_skips_new_chat_title(tmp_path: Path) -> None:
    session_file = tmp_path / "new.json"
    session_file.write_text(json.dumps({
        "version": 3,
        "requests": [
            {"message": {"text": "test"}, "response": [{"value": "ok"}]},
        ],
    }))

    index_entry = {"title": "New Chat", "timing": {"created": 1770688423673}}
    result = _parse_chat_json(session_file, index_entry)
    assert result is not None
    assert result.meta.summary is None  # "New Chat" should be filtered out


def test_parse_chat_jsonl(tmp_path: Path) -> None:
    session_file = tmp_path / "jsonl-sess.jsonl"
    lines = [
        json.dumps({"kind": 0, "v": {"sessionId": "jsonl-sess", "requests": []}}),
        json.dumps({"kind": 1, "k": ["responderUsername"], "v": "GitHub Copilot"}),
        json.dumps({"kind": 2, "v": [
            {
                "requestId": "r1",
                "message": {"text": "What is Python?"},
                "response": [{"value": "A programming language."}],
            }
        ]}),
    ]
    session_file.write_text("\n".join(lines))

    result = _parse_chat_jsonl(session_file, {})
    assert result is not None
    assert result.meta.id == "vscode-jsonl-sess"
    assert len(result.turns) == 1
    assert result.turns[0].user_message == "What is Python?"
