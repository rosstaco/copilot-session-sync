# copilot-session-sync

Sync Copilot CLI sessions from devcontainers to your host machine.

When you use GitHub Copilot CLI inside devcontainers, each container gets its own session history. This tool scans all your Docker containers, finds scattered Copilot sessions, and consolidates them into your host's `~/.copilot/session-store.db` — giving you unified, searchable history.

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
2. **Shows** a summary table of new sessions found (with summaries, turn counts, source containers)
3. **Asks** for your confirmation before making changes
4. **Syncs** session-state directories and indexes conversations into `session-store.db`

## How it works

- Probes common devcontainer user home directories (`/home/vscode`, `/home/node`, `/home/codespace`, `/root`) plus any custom users from `/etc/passwd`
- Reads `workspace.yaml` for session metadata and `events.jsonl` for conversation turns
- Deduplicates by session ID — safe to run repeatedly
- Auto-backs up `session-store.db` before any writes
- Read-only from the container's perspective (uses `docker cp`)
