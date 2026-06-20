#!/usr/bin/env python3
"""Server monitor: GPU status, training progress, system resources.

Usage:
    python server_monitor.py                    # Full status report
    python server_monitor.py --gpu              # GPU only
    python server_monitor.py --train            # Training processes only
    python server_monitor.py --system           # System resources only
    python server_monitor.py --json             # JSON output
    python server_monitor.py --watch            # Continuous monitoring (every 30s)
    python server_monitor.py --watch --interval 60  # Custom interval
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# Fix Windows console encoding for emoji/unicode
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def ensure_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko
        return paramiko

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

def ssh_exec(host, port, username, password, command, timeout=15):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=int(port), username=username, password=password, timeout=10)
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        return stdout.read().decode("utf-8", errors="replace").strip()
    finally:
        ssh.close()

def get_gpu_info(ssh_cfg):
    """Get GPU status via nvidia-smi."""
    raw = ssh_exec(**ssh_cfg, command="nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,fan.speed --format=csv,noheader,nounits")
    if not raw:
        return []
    gpus = []
    for line in raw.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 7:
            gpu = {
                "index": int(parts[0]),
                "name": parts[1],
                "temp_c": int(parts[2]),
                "utilization_pct": int(parts[3]),
                "memory_used_mb": int(parts[4]),
                "memory_total_mb": int(parts[5]),
                "power_draw_w": float(parts[6]),
                "power_limit_w": float(parts[7]) if len(parts) > 7 else None,
                "fan_speed_pct": int(parts[8]) if len(parts) > 8 else None,
            }
            gpu["memory_usage_pct"] = round(gpu["memory_used_mb"] / gpu["memory_total_mb"] * 100, 1) if gpu["memory_total_mb"] > 0 else 0
            gpus.append(gpu)
    return gpus

def get_gpu_processes(ssh_cfg):
    """Get processes using GPU."""
    raw = ssh_exec(**ssh_cfg, command="nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || echo ''")
    if not raw or not raw.strip():
        return []
    procs = []
    for line in raw.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            procs.append({"pid": int(parts[0]), "name": parts[1], "gpu_mem_mb": int(parts[2])})
    return procs

def get_training_processes(ssh_cfg):
    """Find training processes (python with GPU usage)."""
    gpu_procs = get_gpu_processes(ssh_cfg)
    if not gpu_procs:
        return []
    
    pids = [str(p["pid"]) for p in gpu_procs]
    pid_gpu = {str(p["pid"]): p["gpu_mem_mb"] for p in gpu_procs}
    
    raw = ssh_exec(**ssh_cfg, command=f"ps -p {','.join(pids)} -o pid,user,%cpu,%mem,etime,args --no-headers 2>/dev/null || echo ''")
    if not raw or not raw.strip():
        return []
    
    results = []
    for line in raw.strip().split("\n"):
        parts = line.split(None, 5)
        if len(parts) >= 6:
            pid = parts[0]
            results.append({
                "pid": int(pid),
                "user": parts[1],
                "cpu_pct": float(parts[2]),
                "mem_pct": float(parts[3]),
                "elapsed": parts[4],
                "command": parts[5],
                "gpu_mem_mb": pid_gpu.get(pid, 0),
            })
    return results

def get_training_logs(ssh_cfg, pids, lines=5):
    """Get recent log output from training processes."""
    logs = {}
    for pid in pids:
        # Try to get stdout from /proc/PID/fd/1 or check common log locations
        raw = ssh_exec(**ssh_cfg, command=f"tail -n {lines} /proc/{pid}/fd/1 2>/dev/null || echo 'N/A'", timeout=5)
        if raw and raw.strip() != "N/A":
            logs[str(pid)] = raw
    return logs

def get_system_info(ssh_cfg):
    """Get system resource overview."""
    commands = {
        "uptime": "uptime",
        "cpu": "top -bn1 | head -5",
        "memory": "free -h",
        "disk": "df -h / /root/autodl-tmp 2>/dev/null | sort -u",
        "load": "cat /proc/loadavg",
    }
    result = {}
    for key, cmd in commands.items():
        result[key] = ssh_exec(**ssh_cfg, command=cmd, timeout=5)
    return result

def format_gpu_bar(used, total, width=20):
    pct = used / total if total > 0 else 0
    filled = int(pct * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct*100:.0f}%"

def print_report(gpus, training, system, show_all=True):
    print("=" * 60)
    print("  服务器监控报告")
    print("=" * 60)
    
    if gpus:
        print("\n📊 GPU 状态:")
        for gpu in gpus:
            bar = format_gpu_bar(gpu["memory_used_mb"], gpu["memory_total_mb"])
            status = "🔥" if gpu["utilization_pct"] > 80 else "✅" if gpu["utilization_pct"] > 0 else "💤"
            print(f"  {status} GPU {gpu['index']}: {gpu['name']}")
            print(f"     温度: {gpu['temp_c']}°C  |  功耗: {gpu['power_draw_w']}W / {gpu['power_limit_w']}W")
            print(f"     利用率: {gpu['utilization_pct']}%  |  显存: {bar} {gpu['memory_used_mb']}/{gpu['memory_total_mb']} MB")
    
    if training:
        print("\n🏋️ 训练进程:")
        for p in training:
            cmd_short = p['command'][:80] + "..." if len(p['command']) > 80 else p['command']
            print(f"  PID {p['pid']}  |  运行: {p['elapsed']}  |  CPU: {p['cpu_pct']}%  |  GPU显存: {p['gpu_mem_mb']} MB")
            print(f"     {cmd_short}")
    
    if system and show_all:
        print("\n💻 系统资源:")
        if system.get("uptime"):
            print(f"  {system['uptime']}")
        if system.get("memory"):
            for line in system["memory"].split("\n")[:3]:
                print(f"  {line}")
        if system.get("load"):
            print(f"  负载: {system['load']}")
    
    print("\n" + "=" * 60)

def main():
    parser = argparse.ArgumentParser(description="Server GPU & training monitor")
    parser.add_argument("--gpu", action="store_true", help="Show GPU status only")
    parser.add_argument("--train", action="store_true", help="Show training processes only")
    parser.add_argument("--system", action="store_true", help="Show system resources only")
    parser.add_argument("--logs", type=int, default=0, help="Show last N lines of training logs")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval in seconds (default: 30)")
    
    args = parser.parse_args()
    config = load_config()
    
    ssh_cfg = {
        "host": config.get("host", ""),
        "port": config.get("port", 22),
        "username": config.get("username", "root"),
        "password": config.get("password", ""),
    }
    
    if not ssh_cfg["host"]:
        print("Error: No host configured in server_config.json", file=sys.stderr)
        sys.exit(1)
    
    show_all = not (args.gpu or args.train or args.system)
    
    def do_monitor():
        gpus = get_gpu_info(ssh_cfg) if (show_all or args.gpu) else []
        training = get_training_processes(ssh_cfg) if (show_all or args.train) else []
        system = get_system_info(ssh_cfg) if (show_all or args.system) else {}
        
        if args.json:
            print(json.dumps({"gpus": gpus, "training": training, "system": system}, indent=2, ensure_ascii=False))
        else:
            if not args.watch:
                print_report(gpus, training, system, show_all)
            else:
                # Compact format for watch mode
                print(f"\n[{time.strftime('%H:%M:%S')}] ", end="")
                for gpu in gpus:
                    print(f"GPU{gpu['index']}: {gpu['utilization_pct']}% util, {gpu['memory_usage_pct']}% mem, {gpu['temp_c']}°C  ", end="")
                for p in training:
                    print(f"| PID {p['pid']}: {p['elapsed']} ({p['gpu_mem_mb']}MB)")
                if not training:
                    print()
    
    if args.watch:
        print(f"Monitoring every {args.interval}s (Ctrl+C to stop)")
        try:
            while True:
                do_monitor()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
    else:
        do_monitor()

if __name__ == "__main__":
    main()
