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
import subprocess
import sys
import io
import time

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

def load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    return json.load(open(p)) if os.path.exists(p) else {}

def resolve_server(cfg, name=None):
    if name and "servers" in cfg:
        s = cfg["servers"]
        if name in s: return {**cfg.get("defaults", {}), **s[name]}
        print(f"Error: Server '{name}' not found.", file=sys.stderr); sys.exit(1)
    return {"host": cfg.get("host", ""), "port": cfg.get("port", 22),
            "username": cfg.get("username", "root"), "password": cfg.get("password", ""),
            "key_file": cfg.get("key_file", "")}

def _connect(host, port, user, pwd=None, key=None, retries=3):
    import paramiko
    for attempt in range(retries):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw = {"hostname": host, "port": int(port), "username": user, "timeout": 15}
            if key and os.path.exists(os.path.expanduser(key)):
                kw["key_filename"] = os.path.expanduser(key)
            elif pwd:
                kw["password"] = pwd
            else:
                for k in ["~/.ssh/id_rsa", "~/.ssh/id_ed25519", "~/.ssh/id_ecdsa"]:
                    if os.path.exists(os.path.expanduser(k)):
                        kw["key_filename"] = os.path.expanduser(k); break
                else:
                    print("Error: No SSH key or password.", file=sys.stderr); sys.exit(1)
            ssh.connect(**kw)
            transport = ssh.get_transport()
            if transport:
                transport.set_keepalive(15)
            return ssh
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise

def _cmd(ssh, cmd, t=15):
    try:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        return out + err
    except Exception as e:
        return f"Error: {e}"

def detect_tool(ssh):
    """Detect available session tool: tmux > screen > nohup."""
    if "tmux" in _cmd(ssh, "which tmux 2>/dev/null", t=3):
        return "tmux"
    if "screen" in _cmd(ssh, "which screen 2>/dev/null", t=3):
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
        _cmd(ssh, f"cat > {wrapper} << 'SPX'\n#!/bin/bash\n{cd_prefix}{command} 2>&1 | tee {log_file}\nSPX")
        _cmd(ssh, f"chmod +x {wrapper}")
        _cmd(ssh, f"screen -dmS {session} bash {wrapper}")
        _cmd(ssh, f"screen -ls | grep {session} | head -1 | awk '{{print $1}}' > {pid_file} 2>/dev/null")
        print(f"  Started in screen session: {session}")
        print(f"  Attach: screen -r {session}")
    
    else:  # nohup
        wrapper = f"{log_dir}/{name}.sh"
        _cmd(ssh, f"cat > {wrapper} << 'SPX'\n#!/bin/bash\n{cd_prefix}{command}\nSPX")
        _cmd(ssh, f"chmod +x {wrapper}")
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
    tmux_out = _cmd(ssh, "tmux list-sessions 2>/dev/null | grep '^sp_'", t=5)
    # Check screen sessions
    screen_out = _cmd(ssh, "screen -ls 2>/dev/null | grep 'sp_'", t=5)
    # Check nohup PIDs
    pid_files = _cmd(ssh, f"ls {log_dir}/*.pid 2>/dev/null", t=5)
    
    tasks = []
    
    # Parse tmux
    if tmux_out.strip() and "no server" not in tmux_out:
        for line in tmux_out.strip().split("\n"):
            parts = line.split(":")
            if parts:
                session = parts[0].strip()
                name = session.replace("sp_", "")
                pid = _cmd(ssh, f"tmux list-panes -t {session} -F '#{{pane_pid}}' 2>/dev/null", t=3).strip()
                alive = _cmd(ssh, f"ps -p {pid} -o pid= 2>/dev/null", t=3).strip() if pid else ""
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
            pid = _cmd(ssh, f"cat {pf} 2>/dev/null", t=3).strip()
            alive = _cmd(ssh, f"ps -p {pid} -o pid= 2>/dev/null", t=3).strip() if pid else ""
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
    gpu = _cmd(ssh, "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null", t=5)
    if gpu.strip():
        for line in gpu.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                print(f"  GPU: {parts[0]}% util, {parts[1]}/{parts[2]} MB VRAM, {parts[3]}C")
    
    # Show disk
    print("\n--- Disk ---")
    disk = _cmd(ssh, "df -h / /root/autodl-tmp 2>/dev/null | sort -u", t=5)
    for line in disk.strip().split("\n"):
        if line.strip(): print(f"  {line}")
    
    # Show load
    print(f"\n--- Load ---")
    load = _cmd(ssh, "uptime", t=5)
    print(f"  {load.strip()}")
    return 0

# ===== LOGS =====
def show_logs(ssh, name, lines=50, follow=False, log_dir="/tmp/sp_tasks"):
    """View task logs."""
    log_file = f"{log_dir}/{name}.log"
    
    exists = _cmd(ssh, f"test -f {log_file} && echo OK", t=3).strip()
    if "OK" not in exists:
        print(f"Error: Log file not found: {log_file}", file=sys.stderr)
        return 1
    
    if follow:
        print(f"Following {log_file} (Ctrl+C to stop)...")
        # Use a loop since SSH exec_command blocks
        try:
            last_size = 0
            while True:
                size = _cmd(ssh, f"stat -c %s {log_file} 2>/dev/null || stat -f %z {log_file} 2>/dev/null", t=3).strip()
                try:
                    size = int(size)
                except:
                    size = 0
                if size > last_size:
                    new_content = _cmd(ssh, f"tail -c {size - last_size} {log_file}", t=5)
                    print(new_content, end="")
                    last_size = size
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped following.")
    else:
        content = _cmd(ssh, f"tail -{lines} {log_file}", t=10)
        print(content, end="")
    return 0

# ===== STOP =====
def stop_task(ssh, name=None, stop_all=False, log_dir="/tmp/sp_tasks"):
    """Stop background task(s)."""
    if stop_all:
        # Stop all tmux sp_ sessions
        sessions = _cmd(ssh, "tmux list-sessions 2>/dev/null | grep '^sp_' | cut -d: -f1", t=5)
        for s in sessions.strip().split("\n"):
            if s.strip():
                _cmd(ssh, f"tmux kill-session -t {s.strip()} 2>/dev/null")
                print(f"  Stopped tmux: {s.strip()}")
        
        # Stop all screen sp_ sessions
        screens = _cmd(ssh, "screen -ls 2>/dev/null | grep 'sp_' | awk '{print $1}'", t=5)
        for s in screens.strip().split("\n"):
            if s.strip():
                _cmd(ssh, f"screen -X -S {s.strip()} quit 2>/dev/null")
                print(f"  Stopped screen: {s.strip()}")
        
        # Stop all nohup PIDs
        pids = _cmd(ssh, f"cat {log_dir}/*.pid 2>/dev/null", t=5)
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
    pid = _cmd(ssh, f"cat {log_dir}/{name}.pid 2>/dev/null", t=3).strip()
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
    if not args.command:
        pa.print_help(); return 1
    
    cfg = load_config()
    srv = resolve_server(cfg, args.server)
    if not srv.get("host"):
        print("Error: No host.", file=sys.stderr); return 1
    
    try:
        import paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko
    
    ssh = _connect(srv["host"], srv.get("port", 22), srv.get("username", "root"),
                   srv.get("password", ""), srv.get("key_file", ""))
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

