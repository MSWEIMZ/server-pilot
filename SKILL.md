---
name: server-pilot
description: "Remote server management via SSH. Use when the user types /server-pilot, mentions server/SSH/GPU/训练/服务器, or wants to run commands on a remote server. Supports password and key auth, multi-server configs, GPU monitoring, training tracking, file operations, and background tasks."
---

# Server Pilot

## First Action When Triggered

When this skill is triggered (via /server-pilot or any server-related request):

1. **Read config**: Read `scripts/server_config.json` to get server info
2. **Choose the narrowest scope**: If the user names a server, GPU, experiment, task, PID, log, or path, inspect only that target. Run a full multi-server monitor only for a global resource overview or scheduling decision.
3. **Connect and inspect**: Use the relevant targeted command. On Windows/PowerShell, set UTF-8 separately when needed; do not rely on the CMD-only `chcp 65001 & ...` form.
4. **Start dashboard only when requested**: Do not launch a background dashboard for a status check or one-off command. Start it only when the user asks for the UI or a long-lived monitoring view.
5. **If connection fails**: Report `UNKNOWN`; do not infer task failure or GPU availability, overwrite configuration, or bypass host-key verification.

### Config File Format (`scripts/server_config.json`)

Single server:

```json
{
  "host": "your-server.com",
  "port": 22,
  "username": "root",
  "password": "<stored-secret-not-for-commit>"
}
```

Multi server:

```json
{
  "defaults": { "username": "root" },
  "servers": {
    "gpu-box": { "host": "server1.com", "port": 26628, "password": "<stored-secret-not-for-commit>" },
    "train-box": { "host": "server2.com", "port": 22, "key_file": "<path-to-private-key>" }
  }
}
```

When the user provides new server info, validate it first and ask before persisting it. Prefer an environment variable or OS credential store; never place a real password, token, cookie, or private key in examples, logs, or replies.

### Connection Method

This skill uses **paramiko** (Python SSH library), NOT native ssh command.

- Passwords are read from the protected local config or an SSH key; do not print their values
- Includes keepalive (15s) and auto-retry (3 attempts)
- Works on Windows without sshpass or key setup
- Before the first connection, verify the server fingerprint out of band and add it to `scripts/known_hosts`; unknown host keys are rejected.
- Install the dependency explicitly when needed: `python -m pip install paramiko`.

## Server Status

```bash
python scripts/server_monitor.py              # Full status
python scripts/server_monitor.py --gpu        # GPU only
python scripts/server_monitor.py --train --logs # Training + logs
python scripts/server_monitor.py --json       # JSON output
python scripts/server_monitor.py --watch      # Continuous (30s refresh)
```

Keep status scopes distinct:

- **Managed tasks**: jobs registered by `task_mgr`; this is not a complete process inventory.
- **Training processes**: detected ML commands from the process table and GPU compute list, regardless of who launched them.
- **Current-user processes**: processes owned by the configured remote user.
- **All-user processes**: system-wide visibility when permissions allow.
- **CPU processes**: include non-GPU parents, data loaders, launchers, TensorBoard, Jupyter, and helpers, but do not automatically label them as training.

When the user asks for all activity, combine task-manager records, the OS process tree, GPU compute processes, and available logs. Deduplicate by server plus host PID/process identity and show coverage gaps as `UNKNOWN`. Never label an incomplete source such as `myjobs` as “all tasks” or “my training processes”.

## Remote Commands

```bash
python scripts/ssh_exec.py "your command"     # Run any command
python scripts/ssh_exec.py --download /remote/path ./local # Download
python scripts/ssh_exec.py --list-servers     # List configured servers
```

## Upload and Run (IMPORTANT)

When uploading a local file to the server, **NEVER use ssh + heredoc** (PowerShell does not support heredoc syntax).
**Always use upload_and_run.py** for uploading files:

```bash
# Upload only
python scripts/upload_and_run.py ./local_script.py /remote/path/script.py

# Upload and run
python scripts/upload_and_run.py ./local_script.py /remote/path/script.py --run

# Upload, run, with arguments
python scripts/upload_and_run.py ./train.py /root/train.py --run --args "--epochs 100"

# Custom Python interpreter
python scripts/upload_and_run.py ./train.py /root/train.py --run --python /usr/bin/python3
```

This uses SFTP internally, no heredoc or shell escaping issues.

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
```

Stopping or restarting requires explicit confirmation with the exact task/PID and impact. Never use `stop --all` as a routine cleanup command.

After starting a background task, immediately report a launch receipt containing:

- server alias and GPU ID;
- PID or task ID;
- working directory and result directory;
- log path;
- exact status/log command.

Track observations as `RUNNING`, `COMPLETED`, `FAILED`, `STOPPED`, `UNKNOWN`, or `BLOCKED`. A missing process is not proof of completion: verify the remote task exit code, terminal evidence, completion marker, and expected artifacts before reporting success. Keep the remote task exit code, local wrapper/tool error, and scientific gate separate. Never stop or restart a task that was not created for the current request without explicit confirmation.

If a project intentionally uses a nonzero remote exit code for a normally completed negative scientific gate, require that meaning to be predeclared in the experiment contract. Report it as execution completion plus `scientific_gate=FAIL_NEGATIVE`; do not silently treat arbitrary nonzero exits as success.

## Web Dashboard

```bash
python scripts/web/dashboard.py              # Start on port 8765 (auto-skips if already running)
python scripts/web/dashboard.py --port 9000  # Custom port
python scripts/web/dashboard.py --no-browser # Don't auto-open browser
python scripts/web/dashboard.py --bind 0.0.0.0 --allow-remote --token "choose-a-long-random-token" # Explicit remote mode
```

The dashboard listens on `127.0.0.1` by default. Remote binding requires both `--allow-remote` and a Bearer token.

Features: real-time GPU gauges, training process list with epoch/loss/acc parsing, system resources, light/dark theme, CN/EN switch, process detail modal with log viewer.

Dashboard labels and counts must expose their data source and scope. Keep managed-task count, detected training-process count, current-user count, all-user count, and CPU-process count separate. Do not count the same PID/process tree twice across cards; show the observation timestamp and coverage limitations.

## Multi-Server

Use `--server name` to select: `python scripts/server_monitor.py --server gpu-box`
See `scripts/server_config.example.json` for config format.

## Training Log Parsing

Parses `/proc/PID/fd` for: Epoch, Loss, Accuracy, Learning rate, Step, ETA

## Paths

All `scripts/` paths are relative to the canonical source: `C:\Users\WEI\server-pilot\scripts\`.
