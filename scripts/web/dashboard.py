#!/usr/bin/env python3
"""Server Pilot Web Dashboard — multi-server parallel polling edition.

Each server is polled independently in its own background thread.
Switching between servers in the frontend is instant since all
servers always have fresh cached data.
"""
import argparse, json, os, sys, time, threading, webbrowser, re
from http.server import HTTPServer, BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    import socketserver
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server_monitor import load_config, resolve_server, _connect, _cmd, gpu_info, train_procs, parse_logs, sys_info

# ── Enhanced _cmd wrapper with full PATH for container environments ──
_PATH_PREFIX = 'export TERM=dumb; export PATH="$HOME/.local/bin:$HOME/.local/share/bin:$PATH"; '
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07')

def _cmd_dash(ssh, c, t=15):
    """Execute command with full user PATH (including ~/.local/bin)."""
    try:
        _, o, _ = ssh.exec_command(_PATH_PREFIX + c, timeout=t)
        raw = o.read().decode("utf-8", errors="replace").strip()
        return _ANSI_RE.sub('', raw)
    except Exception:
        return ""

# ── Per-server state ──────────────────────────────────────────
# Each server has its own: cached data, SSH connection cache, lock, wake event
_server_states = {}   # {name: {cached, ssh_cache, lock, event, error_count}}
config = {}
poll_interval = 10
_available_servers = []
_shutdown = threading.Event()

# ── Per-server state management ──────────────────────────────
def _empty_cached(name=""):
    return {
        "gpus": [], "training": [], "logs": {}, "system": {},
        "processes": [], "network": [], "tasks": [], "myjobs": None,
        "error": None, "updated": 0, "server_name": name
    }

def _get_state(name):
    if name not in _server_states:
        _server_states[name] = {
            "cached": _empty_cached(name),
            "ssh_cache": {"ssh": None, "host": "", "time": 0},
            "lock": threading.Lock(),
            "event": threading.Event(),
            "error_count": 0,
        }
    return _server_states[name]


# ── Per-server SSH management ────────────────────────────────
def _get_ssh(st, srv):
    """Get or create cached SSH connection for a server. Called under st['lock']."""
    host = srv.get("host", "")
    now = time.time()
    sc = st["ssh_cache"]

    cached_ssh = None
    if sc["ssh"] and sc["host"] == host and now - sc["time"] < 120:
        cached_ssh = sc["ssh"]
        try:
            transport = cached_ssh.get_transport()
            if not (transport and transport.is_active()):
                cached_ssh = None
        except Exception:
            cached_ssh = None

    if cached_ssh:
        # Quick healthcheck
        try:
            _, o, _ = cached_ssh.exec_command("echo alive", timeout=4)
            if o.read().strip() == b"alive":
                sc["time"] = now
                return cached_ssh
        except Exception:
            pass
        st["ssh_cache"] = {"ssh": None, "host": "", "time": 0}

    # Need new connection — release lock during slow connect
    old_ssh = st["ssh_cache"]["ssh"]
    st["ssh_cache"] = {"ssh": None, "host": host, "time": 0}
    st["lock"].release()
    try:
        if old_ssh:
            try: old_ssh.close()
            except: pass
        ssh = _connect(
            host, srv.get("port", 22), srv.get("username", "root"),
            srv.get("password", ""), srv.get("key_file", "")
        )
    except Exception as e:
        st["lock"].acquire()
        raise RuntimeError(f"SSH connect failed: {e}")
    st["lock"].acquire()
    st["ssh_cache"] = {"ssh": ssh, "host": host, "time": time.time()}
    return ssh

def _close_ssh(st):
    """Close and clear cached SSH for a server. Called under st['lock']."""
    old_ssh = st["ssh_cache"]["ssh"]
    st["ssh_cache"] = {"ssh": None, "host": "", "time": 0}
    if old_ssh:
        def _bg():
            try: old_ssh.close()
            except: pass
        threading.Thread(target=_bg, daemon=True).start()


# ── Helpers ───────────────────────────────────────────────────
def get_procs(ssh):
    raw = _cmd(ssh, "ps aux --sort=-%cpu | head -11", t=5)
    out = []
    if raw:
        for l in raw.strip().split("\n")[1:]:
            p = l.split(None, 10)
            if len(p) >= 11:
                out.append({"user": p[0], "pid": p[1], "cpu": p[2], "mem": p[3], "cmd": p[10][:120]})
    return out

def get_net(ssh):
    raw = _cmd(ssh, "cat /proc/net/dev 2>/dev/null | grep -v lo | head -5", t=5)
    nets = []
    if raw:
        for l in raw.strip().split("\n"):
            if ":" in l:
                iface, data = l.split(":", 1)
                p = data.split()
                if len(p) >= 9:
                    nets.append({"iface": iface.strip(), "rx": round(int(p[0]) / 1024), "tx": round(int(p[8]) / 1024)})
    return nets

def describe_task(cmd):
    """Guess what a training process is doing based on its command."""
    desc = ""
    m = re.search(r'(?:python\S*)\s+([\w./-]+\.py)', cmd)
    if m:
        script = m.group(1).split("/")[-1]
        script_map = {
            "train": "Training", "test": "Testing", "eval": "Evaluation",
            "finetune": "Fine-tuning", "pretrain": "Pre-training",
            "inference": "Inference", "predict": "Prediction",
        }
        for kw, label in script_map.items():
            if kw in script.lower():
                desc = label; break
        if not desc:
            desc = script.replace("_", " ").replace(".py", "").title()
    model_kw = {
        "efficientnet": "EfficientNet", "resnet": "ResNet", "vgg": "VGG",
        "bert": "BERT", "gpt": "GPT", "transformer": "Transformer",
        "r2plus1d": "R(2+1)D", "lstm": "LSTM", "cnn": "CNN",
        "yolo": "YOLO", "unet": "UNet", "mobilenet": "MobileNet",
        "vit": "ViT", "clip": "CLIP", "diffusion": "Diffusion",
    }
    cmd_lower = cmd.lower()
    for kw, name in model_kw.items():
        if kw in cmd_lower:
            desc += f" ({name})" if desc else name
            break
    params = {}
    for pattern, key in [
        (r'--num_classes\s+(\d+)', "classes"),
        (r'--epochs?\s+(\d+)', "epochs"),
        (r'--batch_size?\s+(\d+)', "batch"),
        (r'--lr\s+([\d.eE+-]+)', "lr"),
        (r'--mode\s+(\w+)', "mode"),
        (r'--num_layers\s+(\d+)', "layers"),
        (r'--channels\s+(\d+)', "channels"),
    ]:
        pm = re.search(pattern, cmd)
        if pm:
            params[key] = pm.group(1)
    if params:
        desc += " [" + ", ".join(f"{k}:{v}" for k, v in params.items()) + "]"
    return desc or cmd[:80]

def get_tasks(ssh, procs):
    tasks = []
    for p in procs:
        t = dict(p)
        t["description"] = describe_task(p.get("cmd", ""))
        cwd = _cmd(ssh, f"readlink /proc/{p['pid']}/cwd 2>/dev/null", t=3)
        t["cwd"] = cwd if cwd else ""
        status = _cmd(ssh, f"cat /proc/{p['pid']}/status 2>/dev/null | head -3", t=3)
        t["status"] = "running"
        if "zombie" in (status or "").lower():
            t["status"] = "zombie"
        elif "sleeping" in (status or "").lower():
            t["status"] = "sleeping"
        mem_info = _cmd(ssh, f"cat /proc/{p['pid']}/status 2>/dev/null | grep -i vmrss", t=3)
        if mem_info:
            mm = re.search(r'(\d+)\s+kB', mem_info)
            if mm:
                t["rss_mb"] = round(int(mm.group(1)) / 1024)
        tasks.append(t)
    return tasks

def get_process_log(ssh, pid, lines=100):
    """Get process logs. Tries log files first, falls back to /proc."""
    info_cmd = (
        "echo __CMDLINE__; cat /proc/" + str(pid) + "/cmdline 2>/dev/null | tr '\\0' ' '; echo; "
        "echo __CWD__; readlink /proc/" + str(pid) + "/cwd 2>/dev/null; "
        "echo __FD__; ls /proc/" + str(pid) + "/fd/ 2>/dev/null | wc -l; "
        "echo __STATUS__; head -10 /proc/" + str(pid) + "/status 2>/dev/null; "
        "echo __ENV__; cat /proc/" + str(pid) + "/environ 2>/dev/null | tr '\\0' '\\n' | grep -iE 'CUDA|PYTHON|TRAIN|MODEL|GPU' | head -10; "
        "echo __IO__; cat /proc/" + str(pid) + "/io 2>/dev/null; "
        "echo __END__"
    )
    raw = _cmd(ssh, info_cmd, t=10)
    sections = {}
    if raw:
        cur_key = None
        cur_lines = []
        for line in raw.split("\n"):
            s = line.strip()
            if s.startswith("__") and s.endswith("__"):
                if cur_key:
                    sections[cur_key] = "\n".join(cur_lines).strip()
                cur_key = s.strip("_").lower()
                cur_lines = []
            elif cur_key is not None:
                cur_lines.append(line)
        if cur_key:
            sections[cur_key] = "\n".join(cur_lines).strip()

    cmdline = sections.get("cmdline", "")
    cwd = sections.get("cwd", "")
    stdout_text = ""

    script_path = ""
    mode = ""
    mm = re.search(r'--mode\s+(\w+)', cmdline)
    if mm:
        mode = mm.group(1)
    sm = re.search(r'(\S+\.py)', cmdline)
    if sm:
        raw_path = sm.group(1)
        if raw_path.startswith("/"):
            script_path = raw_path
        elif cwd:
            script_path = cwd.rstrip("/") + "/" + raw_path

    candidates = []
    if script_path:
        script_dir = os.path.dirname(script_path)
        raw = _cmd(ssh, "find " + script_dir + " -maxdepth 4 -name '*.log' -mmin -300 -type f -printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -10", t=8)
        if raw:
            for lf in raw.strip().split("\n"):
                parts = lf.split(" ", 1)
                if len(parts) >= 2:
                    candidates.append(parts[1].strip())

    chosen = ""
    if mode:
        for c in candidates:
            if mode.lower() in c.lower():
                chosen = c; break
    if not chosen and script_path:
        script_name = os.path.basename(script_path).replace(".py", "")
        for c in candidates:
            if script_name.lower() in c.lower():
                chosen = c; break
    if not chosen and candidates:
        script_dir_prefix = os.path.dirname(script_path) if script_path else ""
        for c in candidates:
            if script_dir_prefix and c.startswith(script_dir_prefix):
                chosen = c; break

    if chosen:
        content = _cmd(ssh, "tail -" + str(lines) + " '" + chosen + "' 2>/dev/null", t=8)
        if content and content.strip():
            stdout_text = "[Log file: " + chosen + "]\n" + content

    if not stdout_text:
        stdout_text = _cmd(ssh, "timeout 3 tail -" + str(lines) + " /proc/" + str(pid) + "/fd/1 2>/dev/null || echo ''", t=8)

    return {
        "stdout": stdout_text or "",
        "stderr": "",
        "detail": {
            "cmdline": sections.get("cmdline", ""),
            "cwd": cwd,
            "open_files": sections.get("fd", "0"),
            "status": sections.get("status", ""),
            "env": sections.get("env", ""),
            "io": sections.get("io", ""),
        }
    }


# ── myjobs integration ────────────────────────────────────────
def myjobs_info(ssh):
    """Run myjobs -d and parse output into structured data."""
    raw = _cmd_dash(ssh, "myjobs -d 2>/dev/null", t=15)
    if not raw or "not found" in raw.lower() or "no such file" in raw.lower():
        return None

    result = {"gpus": [], "users": [], "processes": [], "my_tasks": [], "raw": raw}

    current_section = None
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("=") or stripped.startswith("\u2500"):
            continue

        if stripped.startswith("GPU:"):
            current_section = "gpu"; continue
        elif stripped.startswith("Users:"):
            current_section = "users"; continue
        elif stripped.startswith("Processes:"):
            current_section = "processes"; continue
        elif stripped.startswith("My Tasks:"):
            current_section = "my_tasks"; continue
        elif stripped.startswith("\u2713") or stripped.startswith("\u2717"):
            result["summary"] = stripped
            current_section = None; continue

        if current_section == "gpu":
            m = re.match(r'cuda:(\d+)\s+.*?(\d+)/(\d+)M\s+(\d+)%\s+(\d+)M\s+free', stripped)
            if m:
                result["gpus"].append({
                    "idx": int(m.group(1)),
                    "mem_used": int(m.group(2)),
                    "mem_total": int(m.group(3)),
                    "util": int(m.group(4)),
                    "mem_free": int(m.group(5)),
                })
        elif current_section == "users":
            parts = stripped.split(None, 4)
            if len(parts) >= 5 and not parts[0].startswith("\u2500") and parts[0] != "User":
                result["users"].append({
                    "user": parts[0], "procs": parts[1], "cpu": parts[2],
                    "ram": parts[3], "tasks": parts[4],
                })
        elif current_section == "processes":
            parts = stripped.split(None, 6)
            if len(parts) >= 5 and not parts[0].startswith("\u2500") and not parts[0].startswith("User"):
                result["processes"].append({
                    "user": parts[0], "pid": parts[1], "cpu": parts[2],
                    "ram": parts[3], "task": parts[4],
                    "start": parts[5] if len(parts) > 5 else "",
                    "run": parts[6] if len(parts) > 6 else "",
                })
        elif current_section == "my_tasks":
            parts = stripped.split(None, 3)
            if len(parts) >= 4 and not parts[0].startswith("\u2500") and parts[0] != "Group":
                result["my_tasks"].append({
                    "group": parts[0], "count": parts[1],
                    "cpu": parts[2], "ram": parts[3],
                })

    return result if result["gpus"] or result["users"] or result["processes"] else None


# ── Poll cycle (per-server) ──────────────────────────────────
def do_poll(name):
    """Single poll cycle for one server. All state guarded by st['lock']."""
    st = _get_state(name)
    srv = resolve_server(config, name)
    if not srv or not srv.get("host"):
        return

    with st["lock"]:
        try:
            ssh = _get_ssh(st, srv)
        except Exception as e:
            st["cached"]["error"] = str(e)
            st["cached"]["updated"] = time.time()
            st["cached"]["server_name"] = name
            _close_ssh(st)
            st["error_count"] = st.get("error_count", 0) + 1
            return
        st["error_count"] = 0

    # Collect data outside lock
    try:
        g = gpu_info(ssh)
        t = train_procs(ssh)

        # Retry once if stale
        had_data = bool(st["cached"].get("gpus") or st["cached"].get("training"))
        if had_data and not g and not t:
            with st["lock"]:
                _close_ssh(st)
                try:
                    ssh = _get_ssh(st, srv)
                except Exception:
                    return
            g = gpu_info(ssh)
            t = train_procs(ssh)

        s = sys_info(ssh)
        pr = get_procs(ssh)
        n = get_net(ssh)
        mj = myjobs_info(ssh)

        # Supplement training from myjobs when nvidia-smi can't detect processes
        # (common in container environments like dev-server)
        if not t and mj and mj.get("processes"):
            ssh_user = srv.get("username", "root")
            my_procs = [p for p in mj["processes"]
                        if ssh_user.startswith(p["user"].rstrip("+"))
                        or p["user"].rstrip("+") == ssh_user[:7]]
            if my_procs:
                t = []
                for p in my_procs:
                    ram_mb = 0
                    ram_str = p.get("ram", "0")
                    rm = re.match(r'([\d.]+)\s*G', ram_str)
                    if rm:
                        ram_mb = int(float(rm.group(1)) * 1024)
                    else:
                        rm2 = re.match(r'(\d+)\s*M', ram_str)
                        if rm2:
                            ram_mb = int(rm2.group(1))
                    t.append({
                        "pid": p["pid"],
                        "user": ssh_user,
                        "cmd": p["task"],
                        "elapsed": p.get("start", ""),
                        "cpu": p.get("cpu", "0"),
                        "vram": ram_mb,
                    })

        # Now collect tasks and logs (after possible myjobs supplement)
        tk = get_tasks(ssh, t)
        lg = {}
        for p in t:
            li = parse_logs(ssh, p["pid"])
            if li:
                lg[str(p["pid"])] = li

        with st["lock"]:
            st["cached"] = {
                "gpus": g, "training": t, "logs": lg, "system": s,
                "processes": pr, "network": n, "tasks": tk, "myjobs": mj,
                "error": None, "updated": time.time(), "server_name": name
            }
    except Exception as e:
        with st["lock"]:
            st["cached"]["error"] = str(e)
            st["cached"]["updated"] = time.time()
            st["cached"]["server_name"] = name
            _close_ssh(st)


def poll_server(name):
    """Background poll loop for one server. Runs in its own thread."""
    st = _get_state(name)
    st["event"].wait(2)  # Initial staggered startup
    while not _shutdown.is_set():
        do_poll(name)
        st["event"].wait(poll_interval)
        st["event"].clear()


# ── HTTP Handler ──────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            hp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
            with open(hp, "r", encoding="utf-8") as f:
                c = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(c.encode("utf-8"))

        elif path == "/api/status":
            # /api/status?server=X or /api/status (all servers)
            srv_name = qs.get("server", [None])[0]
            if srv_name and srv_name in _server_states:
                st = _get_state(srv_name)
                with st["lock"]:
                    data = dict(st["cached"])
                self.send_json(data)
            else:
                # Return all servers' cached data
                result = {}
                for name in _available_servers:
                    st = _get_state(name)
                    with st["lock"]:
                        result[name] = dict(st["cached"])
                self.send_json(result)

        elif path == "/api/log":
            srv_name = qs.get("server", [None])[0]
            pid = qs.get("pid", [None])[0]
            lines = int(qs.get("lines", [200])[0])
            if not pid:
                self.send_json({"error": "pid required"}, 400)
                return
            if not srv_name:
                srv_name = _available_servers[0] if _available_servers else None
            if not srv_name:
                self.send_json({"error": "no server"}, 400)
                return
            try:
                st = _get_state(srv_name)
                with st["lock"]:
                    srv = resolve_server(config, srv_name)
                    ssh = _get_ssh(st, srv)
                log_data = get_process_log(ssh, int(pid), lines)
                log_data["pid"] = pid
                self.send_json(log_data)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        elif path == "/api/servers":
            info = []
            for name in _available_servers:
                st = _get_state(name)
                with st["lock"]:
                    d = st["cached"]
                    info.append({
                        "name": name,
                        "gpus": len(d.get("gpus", [])),
                        "training": len(d.get("training", [])),
                        "error": d.get("error"),
                        "updated": d.get("updated", 0),
                    })
            self.send_json({"servers": info})

        elif path == "/api/refresh":
            srv_name = qs.get("server", [None])[0]
            if srv_name and srv_name in _server_states:
                _get_state(srv_name)["event"].set()
            else:
                # Refresh all
                for name in _available_servers:
                    _get_state(name)["event"].set()
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def log_message(self, *a):
        pass


# ── Main ──────────────────────────────────────────────────────
def port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def main():
    global config, poll_interval, _available_servers

    pa = argparse.ArgumentParser()
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--server", "-s")
    pa.add_argument("--no-browser", action="store_true")
    pa.add_argument("--interval", type=int, default=10)
    a = pa.parse_args()

    if port_in_use(a.port):
        print(f"Dashboard already running at http://localhost:{a.port}")
        if not a.no_browser:
            webbrowser.open(f"http://localhost:{a.port}")
        return

    config = load_config()
    poll_interval = a.interval

    if "servers" in config:
        _available_servers = list(config["servers"].keys())
    elif config.get("host"):
        _available_servers = ["default"]

    if not _available_servers:
        print("Error: No servers configured.", file=sys.stderr)
        sys.exit(1)

    # Initialize state for all servers
    for name in _available_servers:
        _get_state(name)

    print(f"  Server Pilot Dashboard")
    print(f"  Servers: {', '.join(_available_servers)}")
    for name in _available_servers:
        srv = resolve_server(config, name)
        print(f"    {name}: {srv.get('username', 'root')}@{srv.get('host', '')}:{srv.get('port', 22)}")
    print(f"  http://localhost:{a.port}")
    print(f"  Ctrl+C to stop")

    # Start one poll thread per server (staggered start)
    for i, name in enumerate(_available_servers):
        threading.Thread(target=poll_server, args=(name,), daemon=True).start()
        if i < len(_available_servers) - 1:
            time.sleep(0.3)  # Brief stagger to avoid SSH burst

    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()

    s = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        _shutdown.set()
        for name in _available_servers:
            _get_state(name)["event"].set()
        s.server_close()

if __name__ == "__main__":
    main()
