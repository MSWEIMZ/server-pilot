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

## Paths

All `scripts/` paths are relative to this skill directory: `~/.codex/skills/server-pilot/scripts/`.

