#!/usr/bin/env python3
"""Background task manager for remote servers.

Run long tasks on the server that survive SSH disconnection.

Usage:
    python task_mgr.py run "python train.py --epochs 100"          # Run in background (tmux/screen/nohup)
    python task_mgr.py run "python train.py" --name my-training    # Named task
    python task_mgr.py run "python train.py" --tool nohup           # Force nohup
    python task_mgr.py list                                         # List background tasks
    python task_mgr.py status                                       # Show detailed status
    python task_mgr.py logs my-training                             # View task output
    python task_mgr.py logs my-training -f                          # Follow output (tail -f)
    python task_mgr.py logs my-training -n 100                      # Last 100 lines
    python task_mgr.py stop my-training                             # Stop a task
    python task_mgr.py stop --all                                   # Stop all tasks
    python task_mgr.py --server gpu-box list                        # Multi-server
"""

import argparse
import json
import os
import re
import sys
import io
import time

from security import (
    DEFAULT_PROBE_TIMEOUT, connect_ssh, describe_error, exec_remote,
    load_server_config as load_config, msys_unconvert, pooled_exec,
    resolve_server,
)

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

def _connect(host, port, user, pwd=None, key=None, retries=3, host_key_policy=None):
    return connect_ssh(host, port, user, pwd, key, retries=retries, host_key_policy=host_key_policy)

class _PoolSession:
    """Command execution via the connection-pool daemon, with lazy direct fallback.

    The daemon only carries command execution; SFTP work never goes through it.
    When pooled_exec returns None mid-run (daemon went away), a direct SSH
    connection is opened on demand and used from then on.
    """

    def __init__(self, alias, srv):
        self.alias = alias
        self._srv = srv
        self._ssh = None

    @classmethod
    def try_create(cls, alias, srv):
        """Return a session when the daemon answers a real round trip.

        None means the daemon is unavailable: the caller keeps the original
        direct-connect path. A live daemon reporting a hard failure (e.g.
        server unreachable) raises, matching a failed direct _connect.
        The probing command ("true") is side-effect-free, so a daemon that
        accepted it and then went silent is safe to abandon: fall back to
        direct instead of surfacing a pool-internal timeout.
        """
        try:
            if pooled_exec(alias, "true", read_timeout=DEFAULT_PROBE_TIMEOUT) is None:
                return None
        except (TimeoutError, ConnectionError):
            return None
        return cls(alias, srv)

    def direct(self):
        """Return a real SSH connection, connecting once on first use."""
        if self._ssh is None:
            srv = self._srv
            self._ssh = _connect(srv["host"], srv.get("port", 22), srv.get("username", "root"),
                                 srv.get("password", ""), srv.get("key_file", ""),
                                 host_key_policy=srv.get("host_key_policy", ""))
        return self._ssh

    def close(self):
        if self._ssh is not None:
            self._ssh.close()

def _cmd(ssh, cmd, t=None):
    """Run a remote command and return combined stdout+stderr text.

    ``t`` is a client-side read timeout in seconds; ``None`` (default) means
    no timeout, so slow commands are not aborted by the client.
    """
    try:
        if isinstance(ssh, _PoolSession):
            result = pooled_exec(ssh.alias, cmd, read_timeout=t)
            if result is None:
                # Daemon became unavailable mid-run: use the lazy direct path.
                out, err, _ = exec_remote(ssh.direct(), cmd, read_timeout=t)
            else:
                out, err, _ = result
        else:
            out, err, _ = exec_remote(ssh, cmd, read_timeout=t)
        return out + err
    except Exception as e:
        return f"Error: {describe_error(e)}"

def _upload_script(ssh, remote_path, content):
    """Write script content to a local temp file, then upload via SFTP."""
    import tempfile
    if isinstance(ssh, _PoolSession):
        # SFTP never goes through the pool daemon.
        ssh = ssh.direct()
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False, encoding='utf-8')
        tmp.write(content)
        tmp.close()
        sftp = ssh.open_sftp()
        sftp.put(tmp.name, remote_path)
        sftp.close()
        _cmd(ssh, f"chmod +x {remote_path}")
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)

def detect_tool(ssh):
    """Detect available session tool: tmux > screen > nohup."""
    if "tmux" in _cmd(ssh, "which tmux 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT):
        return "tmux"
    if "screen" in _cmd(ssh, "which screen 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT):
        return "screen"
    return "nohup"

# ===== RUN =====
def run_task(ssh, command, name=None, tool=None, workdir=None, log_dir="/tmp/sp_tasks"):
    """Run a command in the background using tmux/screen/nohup."""
    if not tool:
        tool = detect_tool(ssh)
    
    if not name:
        # Auto-generate name from command
        name = re.sub(r'[^a-zA-Z0-9]', '_', command.split()[0]) + "_" + str(int(time.time()))[-6:]
    
    # Sanitize name
    name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    
    # Create log directory
    _cmd(ssh, f"mkdir -p {log_dir}")
    
    log_file = f"{log_dir}/{name}.log"
    pid_file = f"{log_dir}/{name}.pid"
    
    cd_prefix = f"cd {workdir} && " if workdir else ""
    
    if tool == "tmux":
        session = f"sp_{name}"
        # Kill existing session if any
        _cmd(ssh, f"tmux kill-session -t {session} 2>/dev/null")
        cmd = f"{cd_prefix}{command} 2>&1 | tee {log_file}"
        _cmd(ssh, f"tmux new-session -d -s {session} '{cmd}'")
        # Get PID
        _cmd(ssh, f"tmux list-panes -t {session} -F '#{{pane_pid}}' > {pid_file} 2>/dev/null")
        print(f"  Started in tmux session: {session}")
        print(f"  Attach: tmux attach -t {session}")
    
    elif tool == "screen":
        session = f"sp_{name}"
        wrapper = f"{log_dir}/{name}.sh"
        _upload_script(ssh, wrapper, f"#!/bin/bash\n{cd_prefix}{command} 2>&1 | tee {log_file}")
        _cmd(ssh, f"screen -dmS {session} bash {wrapper}")
        _cmd(ssh, f"screen -ls | grep {session} | head -1 | awk '{{print $1}}' > {pid_file} 2>/dev/null")
        print(f"  Started in screen session: {session}")
        print(f"  Attach: screen -r {session}")
    
    else:  # nohup
        wrapper = f"{log_dir}/{name}.sh"
        _upload_script(ssh, wrapper, f"#!/bin/bash\n{cd_prefix}{command}")
        _cmd(ssh, f"nohup bash {wrapper} > {log_file} 2>&1 &")
        pid_out = _cmd(ssh, f"sleep 0.5 && cat /proc/$(ps -o ppid= -p $ 2>/dev/null)/task/*/children 2>/dev/null || echo ''")
        _cmd(ssh, f"pgrep -f '{wrapper}' > {pid_file} 2>/dev/null")
        print(f"  Started with nohup")
    
    print(f"  Log file: {log_file}")
    print(f"  Name: {name}")
    print(f"  Tool: {tool}")
    print(f"\n  Check status: python task_mgr.py list")
    print(f"  View logs:    python task_mgr.py logs {name}")
    print(f"  Stop:         python task_mgr.py stop {name}")
    return 0

# ===== LIST =====
def list_tasks(ssh, log_dir="/tmp/sp_tasks"):
    """List all running background tasks."""
    _cmd(ssh, f"mkdir -p {log_dir}")
    
    # Check tmux sessions
    tmux_out = _cmd(ssh, "tmux list-sessions 2>/dev/null | grep '^sp_'", t=DEFAULT_PROBE_TIMEOUT)
    # Check screen sessions
    screen_out = _cmd(ssh, "screen -ls 2>/dev/null | grep 'sp_'", t=DEFAULT_PROBE_TIMEOUT)
    # Check nohup PIDs
    pid_files = _cmd(ssh, f"ls {log_dir}/*.pid 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT)
    
    tasks = []
    
    # Parse tmux
    if tmux_out.strip() and "no server" not in tmux_out:
        for line in tmux_out.strip().split("\n"):
            parts = line.split(":")
            if parts:
                session = parts[0].strip()
                name = session.replace("sp_", "")
                pid = _cmd(ssh, f"tmux list-panes -t {session} -F '#{{pane_pid}}' 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip()
                alive = _cmd(ssh, f"ps -p {pid} -o pid= 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip() if pid else ""
                tasks.append({"name": name, "tool": "tmux", "pid": pid, "alive": bool(alive)})
    
    # Parse screen
    if screen_out.strip():
        for line in screen_out.strip().split("\n"):
            m = re.search(r'(\d+)\.sp_(\S+)', line)
            if m:
                tasks.append({"name": m.group(2), "tool": "screen", "pid": m.group(1), "alive": True})
    
    # Parse nohup
    if pid_files.strip():
        for pf in pid_files.strip().split("\n"):
            pf = pf.strip()
            if not pf: continue
            name = os.path.basename(pf).replace(".pid", "")
            # Skip if already found via tmux/screen
            if any(t["name"] == name for t in tasks): continue
            pid = _cmd(ssh, f"cat {pf} 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip()
            alive = _cmd(ssh, f"ps -p {pid} -o pid= 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip() if pid else ""
            tasks.append({"name": name, "tool": "nohup", "pid": pid, "alive": bool(alive)})
    
    if not tasks:
        print("No background tasks found.")
        return 0
    
    print(f"{'Name':<25} {'Tool':<8} {'PID':<10} {'Status':<10}")
    print("-" * 55)
    for t in tasks:
        status = "RUNNING" if t["alive"] else "STOPPED"
        icon = ">>" if t["alive"] else "[]"
        print(f"[{icon}] {t['name']:<23} {t['tool']:<8} {t['pid']:<10} {status}")
    return 0

# ===== STATUS =====
def task_status(ssh, log_dir="/tmp/sp_tasks"):
    """Show detailed status of all tasks."""
    list_tasks(ssh, log_dir)
    
    # Show GPU usage
    print("\n--- GPU ---")
    gpu = _cmd(ssh, "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT)
    if gpu.strip():
        for line in gpu.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                print(f"  GPU: {parts[0]}% util, {parts[1]}/{parts[2]} MB VRAM, {parts[3]}C")
    
    # Show disk
    print("\n--- Disk ---")
    disk = _cmd(ssh, "df -h / /root/autodl-tmp 2>/dev/null | sort -u", t=DEFAULT_PROBE_TIMEOUT)
    for line in disk.strip().split("\n"):
        if line.strip(): print(f"  {line}")
    
    # Show load
    print(f"\n--- Load ---")
    load = _cmd(ssh, "uptime", t=DEFAULT_PROBE_TIMEOUT)
    print(f"  {load.strip()}")
    return 0

# ===== LOGS =====
def show_logs(ssh, name, lines=50, follow=False, log_dir="/tmp/sp_tasks"):
    """View task logs."""
    log_file = f"{log_dir}/{name}.log"
    
    exists = _cmd(ssh, f"test -f {log_file} && echo OK", t=DEFAULT_PROBE_TIMEOUT).strip()
    if "OK" not in exists:
        print(f"Error: Log file not found: {log_file}", file=sys.stderr)
        return 1
    
    if follow:
        print(f"Following {log_file} (Ctrl+C to stop)...")
        # Use a loop since SSH exec_command blocks
        try:
            last_size = 0
            while True:
                size = _cmd(ssh, f"stat -c %s {log_file} 2>/dev/null || stat -f %z {log_file} 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip()
                try:
                    size = int(size)
                except:
                    size = 0
                if size > last_size:
                    new_content = _cmd(ssh, f"tail -c {size - last_size} {log_file}", t=DEFAULT_PROBE_TIMEOUT)
                    print(new_content, end="")
                    last_size = size
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped following.")
    else:
        content = _cmd(ssh, f"tail -{lines} {log_file}", t=DEFAULT_PROBE_TIMEOUT)
        print(content, end="")
    return 0

# ===== STOP =====
def stop_task(ssh, name=None, stop_all=False, log_dir="/tmp/sp_tasks"):
    """Stop background task(s)."""
    if stop_all:
        # Stop all tmux sp_ sessions
        sessions = _cmd(ssh, "tmux list-sessions 2>/dev/null | grep '^sp_' | cut -d: -f1", t=DEFAULT_PROBE_TIMEOUT)
        for s in sessions.strip().split("\n"):
            if s.strip():
                _cmd(ssh, f"tmux kill-session -t {s.strip()} 2>/dev/null")
                print(f"  Stopped tmux: {s.strip()}")
        
        # Stop all screen sp_ sessions
        screens = _cmd(ssh, "screen -ls 2>/dev/null | grep 'sp_' | awk '{print $1}'", t=DEFAULT_PROBE_TIMEOUT)
        for s in screens.strip().split("\n"):
            if s.strip():
                _cmd(ssh, f"screen -X -S {s.strip()} quit 2>/dev/null")
                print(f"  Stopped screen: {s.strip()}")
        
        # Stop all nohup PIDs
        pids = _cmd(ssh, f"cat {log_dir}/*.pid 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT)
        for pid in pids.strip().split("\n"):
            if pid.strip():
                _cmd(ssh, f"kill {pid.strip()} 2>/dev/null")
                print(f"  Stopped PID: {pid.strip()}")
        
        # Cleanup
        _cmd(ssh, f"rm -f {log_dir}/*.pid 2>/dev/null")
        print("\nAll tasks stopped.")
        return 0
    
    if not name:
        print("Error: Specify task name or use --all", file=sys.stderr)
        return 1
    
    # Try tmux
    _cmd(ssh, f"tmux kill-session -t sp_{name} 2>/dev/null")
    # Try screen
    _cmd(ssh, f"screen -X -S sp_{name} quit 2>/dev/null")
    # Try PID file
    pid = _cmd(ssh, f"cat {log_dir}/{name}.pid 2>/dev/null", t=DEFAULT_PROBE_TIMEOUT).strip()
    if pid:
        _cmd(ssh, f"kill {pid} 2>/dev/null")
        _cmd(ssh, f"rm -f {log_dir}/{name}.pid 2>/dev/null")
    
    print(f"Stopped task: {name}")
    return 0

def main():
    pa = argparse.ArgumentParser(description="Background task manager")
    sub = pa.add_subparsers(dest="command")
    
    p_run = sub.add_parser("run", help="Run command in background")
    p_run.add_argument("user_command", help="Command to run")
    p_run.add_argument("--name", "-n", help="Task name")
    p_run.add_argument("--tool", "-t", choices=["tmux", "screen", "nohup"], help="Session tool")
    p_run.add_argument("--workdir", "-w", help="Working directory")
    
    sub.add_parser("list", help="List tasks")
    sub.add_parser("status", help="Detailed status")
    
    p_log = sub.add_parser("logs", help="View task logs")
    p_log.add_argument("name", help="Task name")
    p_log.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    p_log.add_argument("-f", "--follow", action="store_true", help="Follow output")
    
    p_stop = sub.add_parser("stop", help="Stop task(s)")
    p_stop.add_argument("name", nargs="?", help="Task name")
    p_stop.add_argument("--all", action="store_true", help="Stop all tasks")
    
    pa.add_argument("--server", "-s", help="Server name")
    
    args = pa.parse_args()
    # Undo Git Bash path rewriting on the remote workdir argument
    if getattr(args, "workdir", None):
        args.workdir = msys_unconvert(args.workdir)
    if not args.command:
        pa.print_help(); return 1
    
    cfg = load_config()
    srv = resolve_server(cfg, args.server)
    if not srv.get("host"):
        print("Error: No host.", file=sys.stderr); return 1
    
    # Pool daemon alias: the --server name, or "default" for a flat config.
    alias = args.server if (args.server and "servers" in cfg) else "default"
    # Prefer the pool daemon for command execution; fall back to the original
    # direct connection when the daemon is unavailable.
    ssh = _PoolSession.try_create(alias, srv) or _connect(
        srv["host"], srv.get("port", 22), srv.get("username", "root"),
        srv.get("password", ""), srv.get("key_file", ""), host_key_policy=srv.get("host_key_policy", ""))
    try:
        if args.command == "run":
            return run_task(ssh, args.user_command, args.name, args.tool, args.workdir)
            # Fix: use the actual command string
        elif args.command == "list":
            return list_tasks(ssh)
        elif args.command == "status":
            return task_status(ssh)
        elif args.command == "logs":
            return show_logs(ssh, args.name, args.lines, args.follow)
        elif args.command == "stop":
            return stop_task(ssh, args.name, args.all)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr); return 1
    finally:
        ssh.close()

if __name__ == "__main__":
    sys.exit(main() or 0)

