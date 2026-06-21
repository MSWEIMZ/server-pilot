---
name: server-pilot
description: "Remote server management for Codex: SSH commands, GPU monitoring, training progress tracking with epoch/loss/accuracy parsing, and file transfer. Supports multi-server configs, SSH key auth, and continuous monitoring / watch mode. Use when the user asks to check server status, GPU usage, training progress, run remote commands, upload/download files, or mentions server/SSH/GPU/training/监控/服务器/训练."
---

# Server Pilot

Execute remote commands, monitor GPU, and track training progress via SSH.

## Quick Reference

| Action | Command |
|--------|---------|
| Full status | `python scripts/server_monitor.py` |
| GPU only | `python scripts/server_monitor.py --gpu` |
| Training + logs | `python scripts/server_monitor.py --train --logs` |
| JSON output | `python scripts/server_monitor.py --json` |
| Remote command | `python scripts/ssh_exec.py "command"` |
| Upload file | `python scripts/ssh_exec.py --upload ./local /remote/path` |
| Download file | `python scripts/ssh_exec.py --download /remote/path ./local` |
| List servers | `python scripts/ssh_exec.py --list-servers` |

## Windows Note

Prefix monitor commands with `chcp 65001` to avoid emoji encoding errors:
```
chcp 65001 & python scripts/server_monitor.py
```

## Multi-Server

Configure `scripts/server_config.json` with a `servers` key. Use `--server name` to select.
See `references/workflow.md` for examples.

## Auth

SSH key auth is preferred. Falls back to password. Auto-discovers `~/.ssh/id_rsa` or `id_ed25519`.

## Training Log Parsing

The `--logs` flag parses `/proc/PID/fd` for common training output patterns:
- Epoch: `Epoch 5/100`, `[5/100]`
- Loss: `loss: 0.1234`, `Loss= 0.1234`
- Accuracy: `acc: 95.2`, `accuracy=0.952`
- Learning rate: `lr: 1e-4`
- Step/iteration: `Step 100/5000`
- ETA: `ETA: 2h30m`


## Web Dashboard

Launch a real-time web dashboard to monitor your server in the browser:

`ash
python scripts/web/dashboard.py              # Start on port 8765
python scripts/web/dashboard.py --port 9000  # Custom port
python scripts/web/dashboard.py --server gpu-box  # Multi-server
python scripts/web/dashboard.py --no-browser  # Don't auto-open
`

Features:
- Real-time GPU status (temp, power, VRAM, utilization)
- Training process list with epoch/loss/accuracy parsing
- System resources (CPU, memory, disk, load)
- Auto-refreshes every 10 seconds
- Dark theme, responsive design


## File Operations

View, edit, browse, search, and sync remote files.

```bash
# View file
python scripts/file_ops.py cat /remote/file
python scripts/file_ops.py cat /remote/file -n 50 -t

# List directory
python scripts/file_ops.py ls /remote/dir
python scripts/file_ops.py ls /remote/dir -t            # Tree

# Edit remote file (downloads, opens editor, uploads with backup)
python scripts/file_ops.py edit /remote/file
python scripts/file_ops.py edit /remote/file --editor code

# Search files
python scripts/file_ops.py search /remote/dir --name "*.py" --grep "train"

# Sync directories
python scripts/file_ops.py sync-up ./local/dir /remote/dir
python scripts/file_ops.py sync-down /remote/dir ./local/dir

# Compare files
python scripts/file_ops.py diff /remote/file ./local/file
```

## Background Tasks

Run commands that survive SSH disconnect, list/status, view logs, and stop.

```bash
python scripts/task_mgr.py run "python train.py --epochs 100"
python scripts/task_mgr.py run "python train.py" --name my-training --tool screen --workdir /root/project
python scripts/task_mgr.py list
python scripts/task_mgr.py status
python scripts/task_mgr.py logs my-training
python scripts/task_mgr.py logs my-training -f
python scripts/task_mgr.py logs my-training -n 200
python scripts/task_mgr.py stop my-training
python scripts/task_mgr.py stop --all
python scripts/task_mgr.py --server gpu-box run "python train.py"
```
## Paths

All `scripts/` paths are relative to this skill directory: `~/.codex/skills/server-pilot/scripts/`.


