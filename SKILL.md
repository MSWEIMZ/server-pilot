---
name: server-pilot
description: "Remote server management via SSH. Use when the user types /server-pilot, mentions server/SSH/GPU/训练/服务器, or wants to run commands on a remote server. Supports password and key auth, multi-server configs, GPU monitoring, training tracking, file operations, and background tasks."
---

# Server Pilot

## First Action When Triggered

When this skill is triggered (via /server-pilot or any server-related request):

1. **Read config**: Read `scripts/server_config.json` to get server info
2. **Connect and show status**: Run `chcp 65001 & python scripts/server_monitor.py`
3. **Start dashboard**: Run `chcp 65001 & python scripts/web/dashboard.py --no-browser` in background, tell user to visit `http://localhost:8765`
4. **If connection fails**: Ask user for new host/port/username/password, update `scripts/server_config.json`, retry

### Config File Format (`scripts/server_config.json`)

Single server:

```json
{
  "host": "your-server.com",
  "port": 22,
  "username": "root",
  "password": "your-password"
}
```

Multi server:

```json
{
  "defaults": { "username": "root" },
  "servers": {
    "gpu-box": { "host": "server1.com", "port": 26628, "password": "pass1" },
    "train-box": { "host": "server2.com", "port": 22, "password": "pass2" }
  }
}
```

When user provides new server info, **immediately update** `scripts/server_config.json` and confirm connection works.

### Connection Method

This skill uses **paramiko** (Python SSH library), NOT native ssh command.

- Passwords are read from config file automatically, no interactive input needed
- Includes keepalive (15s) and auto-retry (3 attempts)
- Works on Windows without sshpass or key setup

## Server Status

```bash
python scripts/server_monitor.py              # Full status
python scripts/server_monitor.py --gpu        # GPU only
python scripts/server_monitor.py --train --logs # Training + logs
python scripts/server_monitor.py --json       # JSON output
python scripts/server_monitor.py --watch      # Continuous (30s refresh)
```

## Remote Commands

```bash
python scripts/ssh_exec.py "your command"     # Run any command
python scripts/ssh_exec.py --upload ./local /remote/path   # Upload
python scripts/ssh_exec.py --download /remote/path ./local # Download
python scripts/ssh_exec.py --list-servers     # List configured servers
```

## File Operations

```bash
python scripts/file_ops.py cat /remote/file              # View file
python scripts/file_ops.py cat /remote/file -n 50 -t     # Last 50 lines
python scripts/file_ops.py ls /remote/dir                # List dir
python scripts/file_ops.py ls /remote/dir -t             # Tree view
python scripts/file_ops.py edit /remote/file             # Edit locally, upload with backup
python scripts/file_ops.py search /remote/dir --name "*.py" --grep "train"
python scripts/file_ops.py sync-up ./local/dir /remote/dir     # Upload directory
python scripts/file_ops.py sync-down /remote/dir ./local/dir   # Download directory
python scripts/file_ops.py diff /remote/file ./local/file      # Compare
python scripts/file_ops.py big-upload ./big.zip /remote/big.zip   # Large file upload with progress and resume
python scripts/file_ops.py big-download /remote/big.zip ./big.zip # Large file download with progress and resume
```

## Background Tasks

Run commands that survive SSH disconnect (uses tmux > screen > nohup auto-detect):

```bash
python scripts/task_mgr.py run "python train.py --epochs 100"           # Run in background
python scripts/task_mgr.py run "python train.py" --name train-v1 --tool screen --workdir /root/project
python scripts/task_mgr.py list                                         # List tasks
python scripts/task_mgr.py status                                       # Detailed status (GPU + disk + load)
python scripts/task_mgr.py logs train-v1                                # View logs
python scripts/task_mgr.py logs train-v1 -f                             # Follow logs
python scripts/task_mgr.py logs train-v1 -n 200                         # Last 200 lines
python scripts/task_mgr.py stop train-v1                                # Stop task
python scripts/task_mgr.py stop --all                                   # Stop all
```

## Web Dashboard

```bash
python scripts/web/dashboard.py              # Start on port 8765 (auto-skips if already running)
python scripts/web/dashboard.py --port 9000  # Custom port
python scripts/web/dashboard.py --no-browser # Don't auto-open browser
```

Features: real-time GPU gauges, training process list with epoch/loss/acc parsing, system resources, light/dark theme, CN/EN switch, process detail modal with log viewer.

## Multi-Server

Use `--server name` to select: `python scripts/server_monitor.py --server gpu-box`
See `scripts/server_config.example.json` for config format.

## Training Log Parsing

Parses `/proc/PID/fd` for: Epoch, Loss, Accuracy, Learning rate, Step, ETA

## Paths

All `scripts/` paths are relative to: `~/.codex/skills/my-server-ssh/scripts/`