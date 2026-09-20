---
name: server-pilot
description: "Remote server management via SSH. Use when the user types /server-pilot, mentions server/SSH/GPU/训练/服务器, or wants to run commands on a remote server. Supports password and key auth, multi-server configs, GPU monitoring, training tracking, file operations, and background tasks."
---

# Server Pilot

## First Action When Triggered

When this skill is triggered (via /server-pilot or any server-related request):

1. **Read config**: Read `scripts/server_config.json` to get server info
2. **Ensure SSHFS mount**: Run `python scripts/sshfs_mount.py ensure --server <要操作的服务器>` — idempotent and fast. It mounts that server's home directory to a dedicated local drive (auto-assigned letters) if not mounted, refreshes that server's heartbeat, and ensures a shared idle watcher is running. After ~45 minutes with no skill activity on a server, only that server's drive is auto-unmounted. Other commands: `status`, `unmount --server <name>`, `unmount --all`. rclone binary/config/cache locations come from env vars or `scripts/local_config.json` (gitignored; keys: rclone_path, rclone_config, sshfs_cache_dir, known_hosts_file). Edit code directly on the mounted drive; builds and training always run on the server via SSH.
3. **Choose the narrowest scope**: If the user names a server, GPU, experiment, task, PID, log, or path, inspect only that target. Run a full multi-server monitor only for a global resource overview or scheduling decision.
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
python scripts/ssh_exec.py --timeout 120 "slow command"    # Bounded read timeout
python scripts/ssh_exec.py --connect-timeout 30 "command"  # Handshake timeout only
```

### Connection pool daemon (`scripts/sp_daemon.py`)

All command-execution paths (`ssh_exec`, `server_monitor`, `task_mgr`, `file_ops` cat/ls/search, `upload_and_run --run`) first try a local pool daemon before opening a direct SSH connection:

- **Lazy connect**: the daemon holds zero connections until a command actually targets a server. Configured-but-unused servers cost nothing.
- **Reuse**: subsequent commands to the same server skip the handshake (~0.1s instead of ~0.7-3s).
- **Auto-release**: a connection idle >30 min (env `SP_DAEMON_CONN_IDLE`) is closed; when no connections remain and no requests arrive for 30 min (env `SP_DAEMON_EXIT_IDLE`), the daemon exits entirely. The next command auto-respawns it.
- **Fallback**: if the daemon is unavailable, scripts fall back to the original direct connection with identical behavior. If the daemon is running and reports a hard failure (e.g. server unreachable), that error is surfaced, NOT masked by a silent direct retry.
- **Scope**: only command execution; SFTP transfers always use a direct connection.
- Disable globally with `SP_POOL=off`.
- Inspect: `python -c "import sys; sys.path.insert(0,'scripts'); import security; print(security.daemon_request({'op':'status'}))"` (from the server-pilot root); logs at `<daemon state dir>/daemon.log`.

### Command timeouts (important)

> Git Bash note: MSYS rewrites absolute POSIX-looking arguments (e.g.
> `/home/x` → `<git-root>/home/x`) before Python sees them. All remote path
> arguments of these CLIs (file_ops paths, ssh_exec --upload/--download
> remote side, upload_and_run remote target, task_mgr --workdir) are
> auto-restored by `security.msys_unconvert`; command strings that take free
> text are unaffected. From PowerShell/cmd nothing is rewritten anyway.

Three different timeouts exist and must not be confused:

| Timeout | Flag / setting | Default | Meaning |
| --- | --- | --- | --- |
| SSH connect | `--connect-timeout`, `connect_ssh(timeout=)` | 15s | TCP + SSH handshake only |
| Channel read | `--timeout`, `_cmd(t=)`, `exec_remote(read_timeout=)` | **no timeout** | how long the client waits for the *next* chunk of channel data (idle, not total) |
| Remote command | n/a | none | the remote process itself is never killed by this tool |

- `--timeout` is **not** a command timeout. It only bounds client-side channel
  reads; the remote process keeps running either way.
- The read timeout measures **idleness, not total duration**: every chunk of
  stdout/stderr restarts the clock, so a 40s command that prints every 8s
  survives `--timeout 10`, while a command silent for 10s fails loudly.
- The default is **no read timeout**, so a long build, `unittest discover`,
  training log dump, or `grep` over a large tree will not be cut off.
- `--timeout 0`, `--timeout none`, and `SP_READ_TIMEOUT=none` all mean
  *unlimited*; `SP_READ_TIMEOUT=<seconds>` overrides `--timeout`.
- Short status probes use a bounded 30s read timeout so a hung server fails
  loudly instead of hanging the monitor forever.
- A read timeout is reported as `TimeoutError: no channel data ...`, never as a
  bare empty `SSH Error:`. If a probe fails, `server_monitor.py` prints
  `[WARN] remote probe failed ...` and marks JSON coverage `DEGRADED`; an empty
  section then means "probe failed", not "no GPU / no process".
- For work that must survive a client disconnect, still use `task_mgr.py run`
- A **dropped connection** is not a normal exit: if the transport dies before
  an exit status arrives, the tool raises `ConnectionError: SSH connection was
  interrupted ...` instead of returning a silent `-1`. Treat that as a partial
  result and re-run the read-only probe; never record it as a command result.
- Sessions that die repeatedly are almost always one of: (a) a client-side
  read timeout shorter than the command's silent gap, (b) a foreground
  command outliving the SSH client, or (c) an idle NAT/firewall path dropping
  a connection with no traffic. Check (a) with `--timeout none`, fix (b) with
  `task_mgr.py run`, and for (c) rely on keepalive (15s, already enabled) and
  prefer polling a detached job over holding one long channel open.
  or `nohup ... > log 2>&1 &` and poll the log; a longer timeout does not make
  a foreground command disconnect-proof.

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

`big-upload` / `big-download` only resume when the bytes already present are
byte-identical to the source prefix; otherwise they restart from 0 and say so,
and both verify the finished file by sha256. Never delete a partial file to
"force" a resume -- a mismatched prefix is exactly the silent-corruption case
these checks exist to catch. A non-zero exit from these commands means the
content did not match and the destination must not be trusted.
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

## Long-Running Commands

Never wrap a multi-minute remote command in a short client timeout. Prefer:

```bash
# 1. Detached, with both streams captured
python scripts/ssh_exec.py "cd /path && setsid nohup python train.py > logs/train.log 2>&1 < /dev/null & echo STARTED"

# 2. Poll without blocking
python scripts/ssh_exec.py "tail -n 40 /path/logs/train.log"
```

Redirect **both** stdout and stderr (`> log 2>&1`). A background launch that
captures only stdout loses the traceback, and a silent empty log then cannot be
distinguished from an OOM kill.

## Training Log Parsing

Parses `/proc/PID/fd` for: Epoch, Loss, Accuracy, Learning rate, Step, ETA

## Paths

All `scripts/` paths are relative to the repository root (the directory containing this SKILL.md).
