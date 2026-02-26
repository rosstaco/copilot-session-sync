# copilot-session-sync

## Problem

Copilot CLI sessions are scattered across multiple devcontainer filesystems. Each container has its own `~/.copilot/session-state/` directory with session data (`workspace.yaml`, `events.jsonl`, checkpoints). The host's `session-store.db` only knows about sessions created on the host. There's no built-in way to consolidate everything.

## Approach

Build a Python CLI tool (`copilot-session-sync`) using `uv` that:

1. Scans all Docker containers (running + stopped) for Copilot session data
2. Presents a rich terminal summary of what it found (new sessions, existing, turn counts)
3. Asks the user for confirmation
4. Copies new session-state directories to the host's `~/.copilot/session-state/`
5. Parses `events.jsonl` files and inserts sessions, turns, and FTS5 search entries into `session-store.db`

## Architecture

```
copilot-session-sync/
├── pyproject.toml           # uv project config, entry point
├── src/
│   └── copilot_session_sync/
│       ├── __init__.py
│       ├── cli.py           # Main entry point, rich UI flow
│       ├── scanner.py       # Docker container scanning logic
│       ├── parser.py        # workspace.yaml + events.jsonl parsing
│       └── store.py         # session-store.db merge logic
└── tests/
    ├── __init__.py
    ├── test_parser.py       # Unit tests for YAML/JSONL parsing
    └── test_store.py        # Unit tests for DB merge logic
```

## Data Flow

1. **Scan** → `scanner.py` uses `docker ps -a` to list containers, then `docker cp` to probe common home directories (`/home/vscode`, `/home/node`, `/home/codespace`, `/root`) for `.copilot/session-state/`
2. **Extract** → Copies session-state dirs to a temp staging area via `docker cp`
3. **Parse** → `parser.py` reads `workspace.yaml` (session metadata) and `events.jsonl` (turns) from each session
4. **Diff** → Compare extracted session IDs against existing `session-store.db` entries
5. **Present** → `cli.py` uses `rich` to display a table of new/existing sessions with summaries and turn counts
6. **Confirm** → Prompt user for confirmation
7. **Merge** → `store.py` copies session-state dirs to host, inserts into `session-store.db` (sessions, turns, search_index)
8. **Cleanup** → Remove temp staging area

## Key Design Decisions

- **User home discovery**: Probe `/home/vscode`, `/home/node`, `/home/codespace`, `/root` — and also parse `/etc/passwd` from the container to find any non-standard home dirs
- **Deduplication**: Session IDs are UUIDs, so we deduplicate by ID. If the same session appears in multiple containers (e.g., shared volume), we skip duplicates
- **Backup**: Auto-backup `session-store.db` before any writes
- **events.jsonl parsing**: Extract `user.message` and `assistant.message` events, group into turns. Also extract `session.start` for richer metadata (repository, branch, git_root)
- **FTS5 indexing**: Insert both user messages and assistant responses into `search_index` for full-text search
- **No container modification**: This is read-only from the container's perspective — we only `docker cp` out

## Dependencies

- `rich` — Terminal UI (tables, progress, prompts)
- Standard library only for everything else (sqlite3, json, subprocess, pathlib, tempfile)

## Todos

1. **project-setup** — Initialize uv project with pyproject.toml, src layout, rich dependency
2. **scanner** — Implement Docker container scanning and session extraction
3. **parser** — Implement workspace.yaml and events.jsonl parsing
4. **store** — Implement session-store.db merge with backup, dedup, and FTS5 indexing
5. **cli** — Implement rich CLI flow (scan → present → confirm → sync)
6. **tests** — Unit tests for parser and store modules
7. **polish** — Error handling, edge cases, final testing
