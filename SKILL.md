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

All `scripts/` paths are relative to: `~/.qoderworkcn/skills/server-pilot/scripts/`

## Known Issues — Windows 路径映射 Bug

**问题表现：** 在 Windows 环境下，`task_mgr.py`、`ssh_exec.py`、`upload_and_run.py` 三个脚本存在路径映射 bug。远程路径（如 `/root/...`）会被错误地映射到本地 `E:/Git/root/...`，导致命令在本地执行而非远程服务器，操作失败。

**影响范围：** 所有在 Windows 上使用内置脚本的操作。

**诊断方法：** 如果执行脚本后输出中出现本地路径（如 `E:/Git/root/...`）或命令立即返回无远程执行效果，说明触发了该 bug。

**解决方案：** 遇到此 bug 时，放弃使用内置脚本，切换到下方的 Raw Paramiko Fallback 方案。

---

## Raw Paramiko Fallback (Windows 替代方案)

当内置脚本因路径映射 bug 无法正常工作时，直接使用 Python paramiko 库进行 SSH/SFTP 操作。paramiko 已预装在环境中，可直接使用。

### 基础连接模板

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='your-password', timeout=10)

# ... 执行操作 ...

ssh.close()
```

从配置文件读取连接信息：

```python
import json, os

config_path = os.path.expanduser('~/.qoderworkcn/skills/server-pilot/scripts/server_config.json')
with open(config_path) as f:
    cfg = json.load(f)

# 单服务器模式
host, port = cfg['host'], cfg.get('port', 22)
username, password = cfg['username'], cfg['password']

# 或多服务器模式
# server_cfg = cfg['servers']['gpu-box']
# host, port = server_cfg['host'], server_cfg.get('port', 22)
# username = cfg.get('defaults', {}).get('username', 'root')
# password = server_cfg['password']
```

### SSH 执行命令（替换 `ssh_exec.py`）

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')

stdin, stdout, stderr = ssh.exec_command('nvidia-smi')
print(stdout.read().decode())
print(stderr.read().decode())

ssh.close()
```

获取命令退出码：

```python
stdin, stdout, stderr = ssh.exec_command('python train.py --epochs 100')
exit_code = stdout.channel.recv_exit_status()
output = stdout.read().decode()
if exit_code != 0:
    print(f'Command failed (code {exit_code}): {stderr.read().decode()}')
```

### SFTP 上传/下载（替换 `upload_and_run.py` 的上传部分）

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')
sftp = ssh.open_sftp()

# 上传单个文件
sftp.put('./local_script.py', '/remote/path/script.py')

# 下载单个文件
sftp.get('/remote/path/file.txt', './local_file.txt')

# 先确保远程目录存在
sftp.mkdir('/remote/path/')  # 如已存在会抛出异常，可捕获忽略

sftp.close()
ssh.close()
```

批量上传目录：

```python
import os, paramiko

def upload_dir(sftp, local_dir, remote_dir):
    for root, dirs, files in os.walk(local_dir):
        rel_path = os.path.relpath(root, local_dir)
        rem_path = remote_dir + '/' + rel_path.replace('\\', '/')
        try:
            sftp.mkdir(rem_path)
        except:
            pass
        for f in files:
            local_file = os.path.join(root, f)
            rem_file = rem_path + '/' + f
            sftp.put(local_file, rem_file)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')
sftp = ssh.open_sftp()
upload_dir(sftp, './local_dir', '/remote/dir')
sftp.close()
ssh.close()
```

### SFTP 上传后远程执行（替换 `upload_and_run.py --run`）

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')
sftp = ssh.open_sftp()

# 1. 上传文件
sftp.put('./train.py', '/root/train.py')
sftp.close()

# 2. 执行
stdin, stdout, stderr = ssh.exec_command('cd /root && python train.py --epochs 100')
print(stdout.read().decode())
ssh.close()
```

### 后台任务启动（替换 `task_mgr.py`）

使用 tmux（推荐，SSH 断开后持续运行）：

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')

# 创建新 tmux 会话并启动命令
cmd = (
    "tmux new-session -d -s train-v1 'cd /root/project && python train.py --epochs 100'"
)
ssh.exec_command(cmd)

# 查看任务输出
stdin, stdout, stderr = ssh.exec_command('tmux capture-pane -t train-v1 -p -S -50')
print(stdout.read().decode())

# 停止任务
ssh.exec_command('tmux send-keys -t train-v1 C-c')
ssh.exec_command('tmux kill-session -t train-v1')

# 列出所有 tmux 会话
stdin, stdout, stderr = ssh.exec_command('tmux list-sessions')
print(stdout.read().decode())

ssh.close()
```

使用 nohup（轻量级，不需要 tmux）：

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('host', port=22, username='root', password='...')

cmd = "cd /root/project && nohup python train.py --epochs 100 > train.log 2>&1 &"
ssh.exec_command(cmd)

# 查看日志
stdin, stdout, stderr = ssh.exec_command('tail -20 /root/project/train.log')
print(stdout.read().decode())

ssh.close()
```

### 快速单命令封装

如果需要频繁执行简单命令，可封装为辅助函数直接使用：

```python
import paramiko

def ssh_run(host, port, username, password, cmd):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=username, password=password, timeout=10)
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode()
    ssh.close()
    return out

# 使用
result = ssh_run('host', 22, 'root', 'password', 'nvidia-smi')
print(result)
```