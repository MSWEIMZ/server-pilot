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

import argparse, json, os, re, shlex, sys, io, time

from security import (
    DEFAULT_PROBE_TIMEOUT,
    connect_ssh,
    describe_error,
    exec_remote,
    quote_remote_path,
    validate_pid,
)

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
            "key_file": cfg.get("key_file", ""), "host_key_policy": cfg.get("host_key_policy", "")}

def _connect(host, port, user, pwd=None, key=None, retries=3, host_key_policy=None):
    return connect_ssh(host, port, user, pwd, key, retries=retries, host_key_policy=host_key_policy)

_COMMAND_ERRORS = []
_COMMAND_ERROR_LIMIT = 20


def _record_command_error(command, exc):
    """Remember failed probes instead of silently reporting "no data"."""
    entry = {"command": command[:200], "error": describe_error(exc)}
    _COMMAND_ERRORS.append(entry)
    del _COMMAND_ERRORS[:-_COMMAND_ERROR_LIMIT]


def take_command_errors():
    """Return and clear the collected command errors."""
    errors = list(_COMMAND_ERRORS)
    _COMMAND_ERRORS.clear()
    return errors


def _cmd(ssh, c, t=None):
    """Run a remote probe and return stripped stdout+stderr text.

    ``t`` is a client-side read timeout in seconds; ``None`` (default) means
    no timeout, so a slow or heavily loaded server is not mistaken for an
    idle one.  Failures are recorded via :func:`_record_command_error` rather
    than silently returning an empty string.
    """
    try:
        out, err, _ = exec_remote(ssh, c, read_timeout=t)
        return (out + err).strip()
    except Exception as exc:
        _record_command_error(c, exc)
        return ""

def _num(text, cast=float):
    """Cast an nvidia-smi CSV cell, tolerating [N/A] and other placeholders."""
    try:
        return cast(text)
    except (TypeError, ValueError):
        return None


def gpu_info(ssh):
    r = _cmd(ssh, "nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,fan.speed --format=csv,noheader,nounits")
    if not r: return []
    out = []
    for l in r.split("\n"):
        p = [x.strip() for x in l.split(",")]
        if len(p) >= 7:
            g = {"idx": int(p[0]), "name": p[1], "temp": _num(p[2], int), "util": _num(p[3], int),
                 "mem_u": _num(p[4], int), "mem_t": _num(p[5], int), "pwr": _num(p[6], float),
                 "pwr_max": _num(p[7], float) if len(p) > 7 else None,
                 "fan": _num(p[8], int) if len(p) > 8 else None}
            mem_u, mem_t = g["mem_u"] or 0, g["mem_t"] or 0
            g["mem_pct"] = round(mem_u / mem_t * 100, 1) if mem_t > 0 else 0
            out.append(g)
    return out

def gpu_procs(ssh):
    r = _cmd(ssh, "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null")
    if not r: return []
    result = []
    for l in r.split("\n"):
        parts = [s.strip() for s in l.split(",")]
        if len(parts) >= 3:
            pid, vram = _num(parts[0], int), _num(parts[2], int)
            if pid is None:
                continue
            result.append({"pid": pid, "name": parts[1], "vram": vram or 0})
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

def _marked_sections(raw):
    sections = {}
    current = None
    lines = []
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("__") and stripped.endswith("__"):
            if current:
                sections[current] = "\n".join(lines).strip()
            current = stripped.strip("_").lower()
            lines = []
        elif current:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines).strip()
    return sections


def _regular_log_target(target):
    target = str(target or "").strip()
    return bool(
        target.startswith("/")
        and not target.startswith("/dev/")
        and not target.endswith(" (deleted)")
    )


def _tee_destination(command):
    try:
        parts = shlex.split(str(command or ""))
    except ValueError:
        return ""
    if not parts or os.path.basename(parts[0]) != "tee":
        return ""
    options_done = False
    for part in parts[1:]:
        if not options_done and part == "--":
            options_done = True
            continue
        if not options_done and part.startswith("-"):
            continue
        if _regular_log_target(part):
            return part
    return ""


def resolve_log_source(stdout_target, stderr_target, pipe_peers):
    """Resolve only direct files or same-user tee destinations, never a pipe."""
    for target in (stdout_target, stderr_target):
        if _regular_log_target(target):
            return {
                "kind": "file",
                "path": str(target).strip(),
                "message": "stdout/stderr is redirected to a regular file",
            }

    for line in (pipe_peers or "").splitlines():
        command = line.split("\t", 1)[1] if "\t" in line else line
        destination = _tee_destination(command)
        if destination:
            return {
                "kind": "tee",
                "path": destination,
                "message": "stdout pipe is written by tee",
            }

    return {
        "kind": "unavailable",
        "path": "",
        "message": "stdout/stderr is not backed by a readable log file",
    }


def inspect_log_source(ssh, pid):
    """Inspect fd targets and trace a pipe only to a same-user tee process."""
    pid_s = str(validate_pid(pid))
    targets_raw = _cmd(
        ssh,
        'printf "__STDOUT__\\n"; readlink /proc/' + pid_s + '/fd/1 2>/dev/null; '
        'printf "__STDERR__\\n"; readlink /proc/' + pid_s + '/fd/2 2>/dev/null',
        t=DEFAULT_PROBE_TIMEOUT,
    )
    targets = _marked_sections(targets_raw)
    stdout_target = targets.get("stdout", "")
    stderr_target = targets.get("stderr", "")

    pipe_target = ""
    for target in (stdout_target, stderr_target):
        match = re.fullmatch(r"pipe:\[(\d+)\]", target or "")
        if match:
            pipe_target = f"pipe:[{match.group(1)}]"
            break

    pipe_peers = ""
    if pipe_target:
        pipe_peers = _cmd(
            ssh,
            'printf "__PIPE_PEERS__\\n"; uid=$(id -u); target=' + quote_remote_path(pipe_target) + '; '
            'for fd0 in /proc/[0-9]*/fd/0; do '
            '[ "$(readlink "$fd0" 2>/dev/null)" = "$target" ] || continue; '
            'p=${fd0#/proc/}; p=${p%%/*}; '
            'puid=$(awk \'/^Uid:/{print $2; exit}\' "/proc/$p/status" 2>/dev/null); '
            '[ "$puid" = "$uid" ] || continue; '
            'cmd=$(tr \'\\0\' \' \' < "/proc/$p/cmdline" 2>/dev/null); '
            'printf "%s\\t%s\\n" "$p" "$cmd"; done',
            t=DEFAULT_PROBE_TIMEOUT,
        )

    source = resolve_log_source(stdout_target, stderr_target, pipe_peers)
    source["stdout_target"] = stdout_target
    source["stderr_target"] = stderr_target
    if source["path"]:
        exists = _cmd(
            ssh,
            "test -f " + quote_remote_path(source["path"]) + " && printf OK",
            t=DEFAULT_PROBE_TIMEOUT,
        )
        if exists.strip() != "OK":
            source.update({
                "kind": "unavailable",
                "path": "",
                "message": "resolved log path is not a regular file",
            })
    return source


def tail_process_log(ssh, pid, lines=30):
    source = inspect_log_source(ssh, pid)
    if not source.get("path"):
        return "", source
    lines = max(1, min(int(lines), 1000))
    raw = _cmd(
        ssh,
        "tail -n " + str(lines) + " -- " + quote_remote_path(source["path"]) + " 2>/dev/null",
        t=DEFAULT_PROBE_TIMEOUT,
    )
    if not raw:
        source["message"] = "log file exists but is currently empty"
    return raw or "", source


def parse_log_text(raw):
    """Parse training log text for epoch, loss, accuracy, lr, step, eta."""
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


def parse_logs(ssh, pid):
    """Parse a safe regular-file log source without ever reading a pipe fd."""
    raw, _source = tail_process_log(ssh, pid, 30)
    return parse_log_text(raw)

def sys_info(ssh):
    return {k: _cmd(ssh, c) for k, c in {
        "uptime": "uptime", "mem": "free -h", "load": "cat /proc/loadavg",
        "disk": "df -h / /root/autodl-tmp /home 2>/dev/null | sort -u"
    }.items()}

def bar(u, t, w=20):
    u = u or 0; t = t or 0
    p = u / t if t > 0 else 0; f = int(p * w)
    return "[" + "=" * f + " " * (w - f) + f"] {p*100:.0f}%"

def report(gpus, procs, sys_, logs=None):
    print("=" * 60)
    print("  Server Status Report")
    print("=" * 60)
    if gpus:
        print("\n  GPU:")
        for g in gpus:
            util = g["util"] or 0
            icon = "!!" if util > 80 else "OK" if util > 0 else "--"
            print(f"  [{icon}] GPU {g['idx']}: {g['name']}")
            temp = "N/A" if g["temp"] is None else f"{g['temp']}C"
            pwr = "N/A" if g["pwr"] is None else f"{g['pwr']}W"
            pwr_max = "N/A" if g["pwr_max"] is None else f"{g['pwr_max']}W"
            fan = "N/A" if g["fan"] is None else f"{g['fan']}%"
            print(f"      Temp: {temp}  Power: {pwr}/{pwr_max}  Fan: {fan}")
            mem_u, mem_t = g["mem_u"] or 0, g["mem_t"] or 0
            print(f"      Util: {util}%  VRAM: {bar(mem_u, mem_t)} {mem_u}/{mem_t} MB")
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
          "pwd": srv.get("password", ""), "key": srv.get("key_file", ""), "host_key_policy": srv.get("host_key_policy", "")}
    if not sc["host"]: print("Error: No host.", file=sys.stderr); sys.exit(1)
    all_ = not (args.gpu or args.train or args.system)

    def run():
        take_command_errors()  # discard errors from the previous watch cycle
        ssh = _connect(sc["host"], sc["port"], sc["user"], sc["pwd"], sc["key"], host_key_policy=sc["host_key_policy"])
        try:
            g = gpu_info(ssh) if (all_ or args.gpu) else []
            t = train_procs(ssh) if (all_ or args.train or args.logs) else []
            s = sys_info(ssh) if (all_ or args.system) else {}
            lg = {}
            if (all_ or args.logs) and t:
                for p in t:
                    li = parse_logs(ssh, p['pid'])
                    if li: lg[str(p['pid'])] = li
            errors = take_command_errors()
            if args.json:
                payload = {"gpus": g, "training": t, "logs": lg, "system": s}
                if errors:
                    payload["command_errors"] = errors
                    payload["coverage"] = "DEGRADED"
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                report(g, t, s, lg)
                for entry in errors:
                    print(
                        f"  [WARN] remote probe failed: {entry['error']} :: {entry['command']}",
                        file=sys.stderr,
                    )
                if errors:
                    print(
                        "  [WARN] coverage DEGRADED: empty sections above may mean "
                        "'probe failed', not 'no data'.",
                        file=sys.stderr,
                    )
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

