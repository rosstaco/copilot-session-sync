# copilot-session-sync

Consolidate all your Copilot session history into one searchable database.

When you use GitHub Copilot across devcontainers and VS Code, your conversation history gets scattered — each Docker container has its own session data, and VS Code Copilot Chat stores sessions separately per workspace. This tool finds all of it and merges everything into your host's `~/.copilot/session-store.db`.

## Install

```bash
uv tool install .
```

Or run directly:

```bash
uv run copilot-session-sync
```

## What it does

1. **Scans** all Docker containers (running + stopped) for Copilot CLI session data
2. **Scans** VS Code workspaces for Copilot Chat session history
3. **Shows** a rich summary — container sessions in a table, VS Code sessions in a tree grouped by workspace
4. **Asks** for your confirmation before making changes
5. **Syncs** everything into `session-store.db` with full-text search indexing

## Data sources

| Source | Location | Format |
|---|---|---|
| 🐳 **Copilot CLI** (devcontainers) | `~/.copilot/session-state/` inside containers | `workspace.yaml` + `events.jsonl` |
| 💬 **VS Code Copilot Chat** | `~/Library/Application Support/Code/User/workspaceStorage/` | `state.vscdb` + `chatSessions/*.json` |

## How it works

### Docker container sessions
- Probes common devcontainer user home directories (`/home/vscode`, `/home/node`, `/home/codespace`, `/root`) plus any custom users from `/etc/passwd`
- Reads `workspace.yaml` for session metadata and `events.jsonl` for conversation turns
- Read-only from the container's perspective (uses `docker cp`)

### VS Code Copilot Chat sessions
- Scans all VS Code workspace storage directories on the host
- Reads session metadata (titles, timestamps) from the `state.vscdb` SQLite database
- Parses chat content from JSON session files in `chatSessions/`
- Groups sessions by workspace in a tree view for easy review

### General
- Deduplicates by session ID — safe to run repeatedly
- When duplicate sessions are found, keeps the version with the most turns
- Auto-backs up `session-store.db` before any writes

## Architecture

| Module | Purpose |
|---|---|
| `scanner.py` | Probes Docker containers for `.copilot/session-state/` |
| `vscode.py` | Scans VS Code workspaces for Copilot Chat data |
| `parser.py` | Parses `workspace.yaml` + `events.jsonl` |
| `store.py` | Merges sessions into `session-store.db` with backup + FTS5 |
| `cli.py` | Rich interactive UI: scan → summary → confirm → sync |

## Development

```bash
uv run pytest
```
