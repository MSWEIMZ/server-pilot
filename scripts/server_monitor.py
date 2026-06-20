#!/usr/bin/env python3
"""Server monitor: GPU, training, logs, system. Multi-server support.

Usage:
    python server_monitor.py                     # Full report
    python server_monitor.py --gpu               # GPU only
    python server_monitor.py --train             # Training only
    python server_monitor.py --logs              # Parse epoch/loss/acc
    python server_monitor.py --json              # JSON output
    python server_monitor.py --watch             # Continuous (30s)
    python server_monitor.py --server gpu-box    # Multi-server
    python server_monitor.py --list-servers      # List servers
"""

import argparse, json, os, re, subprocess, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def ensure_paramiko():
    try:
        import paramiko; return paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko; return paramiko

def load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    return json.load(open(p)) if os.path.exists(p) else {}

def resolve_server(cfg, name=None):
    if name and "servers" in cfg:
        s = cfg["servers"]
        if name in s: return {**cfg.get("defaults", {}), **s[name]}
        print(f"Error: Server '{name}' not found. Available: {', '.join(s.keys())}", file=sys.stderr); sys.exit(1)
    return {"host": cfg.get("host", ""), "port": cfg.get("port", 22),
            "username": cfg.get("username", "root"), "password": cfg.get("password", ""),
            "key_file": cfg.get("key_file", "")}

def _connect(host, port, user, pwd=None, key=None):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = {"hostname": host, "port": int(port), "username": user, "timeout": 15}
    if key and os.path.exists(os.path.expanduser(key)):
        kw["key_filename"] = os.path.expanduser(key)
    elif pwd:
        kw["password"] = pwd
    else:
        for k in ["~/.ssh/id_rsa", "~/.ssh/id_ed25519", "~/.ssh/id_ecdsa"]:
            if os.path.exists(os.path.expanduser(k)): kw["key_filename"] = os.path.expanduser(k); break
        else:
            print("Error: No SSH key or password.", file=sys.stderr); sys.exit(1)
    ssh.connect(**kw); return ssh

def _cmd(ssh, c, t=15):
    try:
        _, o, _ = ssh.exec_command(c, timeout=t)
        return o.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return ""

def gpu_info(ssh):
    r = _cmd(ssh, "nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,fan.speed --format=csv,noheader,nounits")
    if not r: return []
    out = []
    for l in r.split("\n"):
        p = [x.strip() for x in l.split(",")]
        if len(p) >= 7:
            g = {"idx": int(p[0]), "name": p[1], "temp": int(p[2]), "util": int(p[3]),
                 "mem_u": int(p[4]), "mem_t": int(p[5]), "pwr": float(p[6]),
                 "pwr_max": float(p[7]) if len(p) > 7 else None, "fan": int(p[8]) if len(p) > 8 else None}
            g["mem_pct"] = round(g["mem_u"] / g["mem_t"] * 100, 1) if g["mem_t"] > 0 else 0
            out.append(g)
    return out

def gpu_procs(ssh):
    r = _cmd(ssh, "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null")
    if not r: return []
    result = []
    for l in r.split("\n"):
        parts = [s.strip() for s in l.split(",")]
        if len(parts) >= 3:
            result.append({"pid": int(parts[0]), "name": parts[1], "vram": int(parts[2])})
    return result

def train_procs(ssh):
    gp = gpu_procs(ssh)
    if not gp: return []
    pv = {str(p["pid"]): p["vram"] for p in gp}
    r = _cmd(ssh, f"ps -p {','.join(pv.keys())} -o pid,user,%cpu,%mem,etime,args --no-headers 2>/dev/null")
    if not r: return []
    out = []
    for l in r.split("\n"):
        p = l.split(None, 5)
        if len(p) >= 6:
            out.append({"pid": int(p[0]), "user": p[1], "cpu": float(p[2]),
                        "mem": float(p[3]), "elapsed": p[4], "cmd": p[5], "vram": pv.get(p[0], 0)})
    return out

def parse_logs(ssh, pid):
    """Parse training stdout/stderr for epoch, loss, accuracy, lr, step, eta."""
    raw = _cmd(ssh, f"tail -30 /proc/{pid}/fd/1 2>/dev/null; tail -30 /proc/{pid}/fd/2 2>/dev/null", t=5)
    if not raw: return None
    info = {}
    for line in reversed(raw.split("\n")):
        line = line.strip()
        if not line: continue
        if 'epoch' not in info:
            m = re.search(r'[Ee]poch[:\s]*(\d+)[/\\](\d+)', line) or re.search(r'\[(\d+)/(\d+)\]', line)
            if m: info['epoch'] = f"{m.group(1)}/{m.group(2)}"
        if 'loss' not in info:
            m = re.search(r'[Ll]oss[:\s=]+([\d.]+)', line)
            if m: info['loss'] = float(m.group(1))
        if 'acc' not in info:
            m = re.search(r'[Aa]cc(?:uracy)?[:\s=]+([\d.]+)%?', line)
            if m: info['acc'] = m.group(1)
        if 'lr' not in info:
            m = re.search(r'[Ll][Rr][:\s=]+([\d.eE+-]+)', line)
            if m: info['lr'] = m.group(1)
        if 'step' not in info:
            m = re.search(r'[Ss]tep[:\s]*(\d+)[/\\](\d+)', line)
            if m: info['step'] = f"{m.group(1)}/{m.group(2)}"
        if 'eta' not in info:
            m = re.search(r'[Ee][Tt][Aa][:\s]*([\d:hm s]+)', line)
            if m: info['eta'] = m.group(1).strip()
        if 'last' not in info and len(line) > 5 and not line.startswith('+') and not line.startswith('='):
            info['last'] = line[:150]
        if len(info) >= 5: break
    return info if info else None

def sys_info(ssh):
    return {k: _cmd(ssh, c, 5) for k, c in {
        "uptime": "uptime", "mem": "free -h", "load": "cat /proc/loadavg",
        "disk": "df -h / /root/autodl-tmp /home 2>/dev/null | sort -u"
    }.items()}

def bar(u, t, w=20):
    p = u / t if t > 0 else 0; f = int(p * w)
    return "[" + "=" * f + " " * (w - f) + f"] {p*100:.0f}%"

def report(gpus, procs, sys_, logs=None):
    print("=" * 60)
    print("  Server Status Report")
    print("=" * 60)
    if gpus:
        print("\n  GPU:")
        for g in gpus:
            icon = "!!" if g["util"] > 80 else "OK" if g["util"] > 0 else "--"
            print(f"  [{icon}] GPU {g['idx']}: {g['name']}")
            print(f"      Temp: {g['temp']}C  Power: {g['pwr']}W/{g['pwr_max']}W  Fan: {g['fan']}%")
            print(f"      Util: {g['util']}%  VRAM: {bar(g['mem_u'],g['mem_t'])} {g['mem_u']}/{g['mem_t']} MB")
    if procs:
        print("\n  Training:")
        for p in procs:
            c = p['cmd'][:80] + "..." if len(p['cmd']) > 80 else p['cmd']
            print(f"  PID {p['pid']}  |  {p['elapsed']}  |  CPU:{p['cpu']}%  |  VRAM:{p['vram']}MB")
            print(f"      {c}")
            if logs and str(p['pid']) in logs:
                lg = logs[str(p['pid'])]
                pts = []
                for k, lbl in [('epoch', 'Epoch'), ('loss', 'Loss'), ('acc', 'Acc'), ('lr', 'LR'), ('step', 'Step'), ('eta', 'ETA')]:
                    if k in lg:
                        v = f"{lg[k]:.4f}" if k == 'loss' else str(lg[k])
                        pts.append(f"{lbl}:{v}")
                if pts: print(f"      -> {' | '.join(pts)}")
                if 'last' in lg: print(f"      >> {lg['last']}")
    if sys_:
        print("\n  System:")
        if sys_.get("uptime"): print(f"  {sys_['uptime']}")
        for l in (sys_.get("mem") or "").split("\n")[:2]:
            if l.strip(): print(f"  {l}")
        if sys_.get("load"): print(f"  Load: {sys_['load']}")
        for l in (sys_.get("disk") or "").split("\n"):
            if l.strip(): print(f"  {l}")
    print("\n" + "=" * 60)

def main():
    pa = argparse.ArgumentParser(description="Server GPU & training monitor")
    pa.add_argument("--gpu", action="store_true")
    pa.add_argument("--train", action="store_true")
    pa.add_argument("--system", action="store_true")
    pa.add_argument("--logs", action="store_true", help="Parse training logs for epoch/loss/acc")
    pa.add_argument("--json", action="store_true")
    pa.add_argument("--watch", action="store_true")
    pa.add_argument("--interval", type=int, default=30)
    pa.add_argument("--server", "-s", help="Server name")
    pa.add_argument("--list-servers", action="store_true")
    args = pa.parse_args()
    cfg = load_config()
    if args.list_servers:
        if "servers" in cfg:
            for n, s in cfg["servers"].items():
                auth = "key" if s.get("key_file") else "password"
                print(f"  {n:15s}  {s.get('username','root')}@{s.get('host','?')}:{s.get('port',22)}  ({auth})")
        else:
            print(f"  default: {cfg.get('host','not configured')}")
        return
    srv = resolve_server(cfg, args.server)
    sc = {"host": srv.get("host", ""), "port": srv.get("port", 22), "user": srv.get("username", "root"),
          "pwd": srv.get("password", ""), "key": srv.get("key_file", "")}
    if not sc["host"]: print("Error: No host.", file=sys.stderr); sys.exit(1)
    all_ = not (args.gpu or args.train or args.system)

    def run():
        ssh = _connect(sc["host"], sc["port"], sc["user"], sc["pwd"], sc["key"])
        try:
            g = gpu_info(ssh) if (all_ or args.gpu) else []
            t = train_procs(ssh) if (all_ or args.train or args.logs) else []
            s = sys_info(ssh) if (all_ or args.system) else {}
            lg = {}
            if (all_ or args.logs) and t:
                for p in t:
                    li = parse_logs(ssh, p['pid'])
                    if li: lg[str(p['pid'])] = li
            if args.json:
                print(json.dumps({"gpus": g, "training": t, "logs": lg, "system": s}, indent=2, ensure_ascii=False))
            else:
                report(g, t, s, lg)
        finally:
            ssh.close()

    if args.watch:
        print(f"Watching every {args.interval}s (Ctrl+C to stop)")
        try:
            while True: run(); time.sleep(args.interval)
        except KeyboardInterrupt: print("\nStopped.")
    else:
        run()

if __name__ == "__main__":
    main()
