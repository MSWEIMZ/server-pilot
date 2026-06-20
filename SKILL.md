---
name: my-server-ssh
description: Connect to the user's personal remote server via SSH and monitor GPU/training status. Use when the user asks to run commands on the server, check server status, check GPU usage, monitor training progress, upload/download files, manage the server, or mentions "服务器", "server", "SSH", "GPU", "训练", "监控". Uses Python paramiko to bypass shell environment issues.
---

# My Server SSH

Execute remote commands and monitor GPU/training status on the user's personal server via SSH.

## Prerequisites

- Python 3.x available in PATH
- paramiko library (auto-installed if missing)

## Quick Commands

### Check server status (recommended first action)

```bash
chcp 65001 & python scripts/server_monitor.py
```

### Check GPU only

```bash
chcp 65001 & python scripts/server_monitor.py --gpu
```

### Check training processes only

```bash
chcp 65001 & python scripts/server_monitor.py --train
```

### JSON output (for programmatic use)

```bash
python scripts/server_monitor.py --json
```

## Run Remote Commands

### Basic command

```bash
python scripts/ssh_exec.py "command here"
```

### With JSON output

```bash
python scripts/ssh_exec.py --json "command here"
```

### Upload a file

```bash
python scripts/ssh_exec.py --upload /local/path /remote/path
```

### Download a file

```bash
python scripts/ssh_exec.py --download /remote/path /local/path
```

### Override server config

```bash
python scripts/ssh_exec.py --host HOST --port PORT --user USER --pass PASS "command"
```

## Server Config

Default connection details are stored in `scripts/server_config.json`.
To update the server (e.g., after restart with new port), edit that JSON file.

## Important Notes

- Always use `chcp 65001` before running monitor scripts to avoid Windows GBK encoding errors with emoji/unicode.
- Scripts use Python paramiko instead of system SSH, avoiding issues when PowerShell is blocked by security software.
- paramiko is auto-installed via pip on first run.
- The `scripts/` path is relative to this skill directory: `~/.codex/skills/my-server-ssh/scripts/`.