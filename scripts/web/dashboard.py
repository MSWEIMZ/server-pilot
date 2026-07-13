#!/usr/bin/env python3
"""Server Pilot Web Dashboard."""
import argparse, json, os, sys, time, threading, webbrowser, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server_monitor import load_config, resolve_server, _connect, _cmd, gpu_info, train_procs, parse_logs, sys_info

cached = {"gpus":[],"training":[],"logs":{},"system":{},"processes":[],"network":[],"tasks":[],"error":None,"updated":0,"server_name":""}
config = {}; server_name = None; poll_interval = 10

def get_procs(ssh):
    raw = _cmd(ssh, "ps aux --sort=-%cpu | head -11", t=5)
    out = []
    if raw:
        for l in raw.strip().split("\n")[1:]:
            p = l.split(None, 10)
            if len(p) >= 11: out.append({"user":p[0],"pid":p[1],"cpu":p[2],"mem":p[3],"cmd":p[10][:120]})
    return out

def get_net(ssh):
    raw = _cmd(ssh, "cat /proc/net/dev 2>/dev/null | grep -v lo | head -5", t=5)
    nets = []
    if raw:
        for l in raw.strip().split("\n"):
            if ":" in l:
                iface, data = l.split(":", 1)
                p = data.split()
                if len(p) >= 9: nets.append({"iface":iface.strip(),"rx":round(int(p[0])/1024),"tx":round(int(p[8])/1024)})
    return nets

def describe_task(cmd):
    """Guess what a training process is doing based on its command."""
    desc = ""
    # Extract script name
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
        if not desc: desc = script.replace("_", " ").replace(".py", "").title()

    # Extract model info
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

    # Extract key params
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
        if pm: params[key] = pm.group(1)

    if params:
        desc += " [" + ", ".join(f"{k}:{v}" for k,v in params.items()) + "]"

    return desc or cmd[:80]

def get_tasks(ssh, procs):
    """Enhanced training task info."""
    tasks = []
    for p in procs:
        t = dict(p)
        t["description"] = describe_task(p.get("cmd",""))
        # Get working directory
        cwd = _cmd(ssh, f"readlink /proc/{p['pid']}/cwd 2>/dev/null", t=3)
        t["cwd"] = cwd if cwd else ""
        # Check if process is a zombie or running
        status = _cmd(ssh, f"cat /proc/{p['pid']}/status 2>/dev/null | head -3", t=3)
        t["status"] = "running"
        if "zombie" in (status or "").lower(): t["status"] = "zombie"
        elif "sleeping" in (status or "").lower(): t["status"] = "sleeping"
        # Memory details
        mem_info = _cmd(ssh, f"cat /proc/{p['pid']}/status 2>/dev/null | grep -i vmrss", t=3)
        if mem_info:
            mm = re.search(r'(\d+)\s+kB', mem_info)
            if mm: t["rss_mb"] = round(int(mm.group(1))/1024)
        tasks.append(t)
    return tasks

def get_process_log(ssh, pid, lines=100):
    """Get process logs. Tries log files first, falls back to /proc."""
    # Step 1: Get basic info (fast, non-blocking)
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

    # Step 2: Find log files (non-blocking alternative to /proc/PID/fd/1 pipe)
    cwd = sections.get("cwd", "")
    stdout_text = ""
    if cwd:
        log_files = _cmd(ssh, "find " + cwd + " -maxdepth 2 -name '*.log' -mmin -300 -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -3", t=8)
        if log_files:
            for lf in log_files.strip().split("\n"):
                parts = lf.split(" ", 1)
                if len(parts) >= 2:
                    log_path = parts[1].strip()
                    content = _cmd(ssh, "tail -" + str(lines) + " '" + log_path + "' 2>/dev/null", t=8)
                    if content and content.strip():
                        stdout_text = "[Log file: " + log_path + "]\n" + content
                        break

    # Fallback: try /proc with timeout (may hang for pipes)
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

# Cached SSH connection for log API
_ssh_cache = {"ssh": None, "host": "", "time": 0}

def get_ssh():
    """Get or create cached SSH connection with keepalive."""
    global _ssh_cache
    srv = resolve_server(config, server_name)
    host = srv.get("host","")
    now = time.time()
    if _ssh_cache["ssh"] and _ssh_cache["host"] == host and now - _ssh_cache["time"] < 60:
        try:
            t = _ssh_cache["ssh"].get_transport()
            if t and t.is_active():
                _ssh_cache["time"] = now
                return _ssh_cache["ssh"]
        except:
            pass
    if _ssh_cache["ssh"]:
        try: _ssh_cache["ssh"].close()
        except: pass
    ssh = _connect(host, srv.get("port",22), srv.get("username","root"), srv.get("password",""), srv.get("key_file",""))
    _ssh_cache = {"ssh": ssh, "host": host, "time": now}
    return ssh

def poll():
    global cached
    time.sleep(2)
    while True:
        try:
            srv = resolve_server(config, server_name)
            ssh = get_ssh()
            g = gpu_info(ssh)
            t = train_procs(ssh)
            lg = {}
            for p in t:
                li = parse_logs(ssh, p["pid"])
                if li: lg[str(p["pid"])] = li
            s = sys_info(ssh)
            pr = get_procs(ssh)
            n = get_net(ssh)
            tk = get_tasks(ssh, t)
            cached = {"gpus":g,"training":t,"logs":lg,"system":s,"processes":pr,"network":n,"tasks":tk,"error":None,"updated":time.time(),"server_name":srv.get("host","")}
        except Exception as e:
            cached["error"] = str(e); cached["updated"] = time.time()
            if _ssh_cache.get("ssh"):
                try: _ssh_cache["ssh"].close()
                except: pass
                _ssh_cache["ssh"] = None
        time.sleep(poll_interval)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            hp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
            with open(hp, "r", encoding="utf-8") as f: c = f.read()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(c.encode("utf-8"))
        elif path == "/api/status":
            self.send_json(cached)
        elif path == "/api/log":
            pid = qs.get("pid", [None])[0]
            lines = int(qs.get("lines", [200])[0])
            if not pid:
                self.send_json({"error": "pid required"}, 400); return
            try:
                ssh = get_ssh()
                log_data = get_process_log(ssh, int(pid), lines)
                log_data["pid"] = pid
                self.send_json(log_data)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def log_message(self, *a): pass

def port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def main():
    global config, server_name, poll_interval
    pa = argparse.ArgumentParser()
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--server", "-s"); pa.add_argument("--no-browser", action="store_true")
    pa.add_argument("--interval", type=int, default=10)
    a = pa.parse_args()

    if port_in_use(a.port):
        print(f"Dashboard already running at http://localhost:{a.port}")
        if not a.no_browser: webbrowser.open(f"http://localhost:{a.port}")
        return

    config = load_config(); server_name = a.server; poll_interval = a.interval
    srv = resolve_server(config, server_name)
    if not srv.get("host"): print("Error: No host.", file=sys.stderr); sys.exit(1)
    print(f"  Server Pilot Dashboard\n  {srv.get('username','root')}@{srv.get('host','')}:{srv.get('port',22)}\n  http://localhost:{a.port}\n  Ctrl+C to stop")
    threading.Thread(target=poll, daemon=True).start()
    if not a.no_browser: threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
    s = HTTPServer(("0.0.0.0", a.port), H)
    try: s.serve_forever()
    except KeyboardInterrupt: print("\nStopped."); s.server_close()

if __name__ == "__main__": main()
