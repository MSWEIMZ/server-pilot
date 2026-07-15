#!/usr/bin/env python3
"""Server Pilot Web Dashboard — multi-server parallel polling edition.

Each server is polled independently in its own background thread.
Switching between servers in the frontend is instant since all
servers always have fresh cached data.
"""
import argparse, hmac, json, os, sys, time, threading, webbrowser, re
from dataclasses import dataclass
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
from security import clamp_log_lines, quote_remote_path, validate_pid

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


@dataclass(frozen=True)
class DashboardSettings:
    bind: str = "127.0.0.1"
    token: str | None = None


def dashboard_settings(bind="127.0.0.1", allow_remote=False, token=None):
    is_loopback = bind in {"127.0.0.1", "::1", "localhost"}
    if not is_loopback and not allow_remote:
        raise ValueError("non-loopback bind requires --allow-remote")
    if not is_loopback and not token:
        raise ValueError("non-loopback bind requires --token")
    return DashboardSettings(bind=bind, token=token if not is_loopback else None)


def request_is_authorized(headers, settings):
    if not settings.token:
        return True
    expected = "Bearer " + settings.token
    return hmac.compare_digest(headers.get("Authorization", ""), expected)


def parse_log_request(query):
    values = query.get("pid", [])
    if not values:
        raise ValueError("pid required")
    line_values = query.get("lines", ["200"])
    return validate_pid(values[0]), clamp_log_lines(line_values[0])


dashboard_security = dashboard_settings()

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
            srv.get("password", ""), srv.get("key_file", ""),
            host_key_policy=srv.get("host_key_policy", "")
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
    raw = _cmd(
        ssh,
        'ps -u "$(id -u)" --sort=-%cpu -o user,pid,%cpu,%mem,args | head -11',
        t=5,
    )
    out = []
    if raw:
        for l in raw.strip().split("\n")[1:]:
            p = l.split(None, 4)
            if len(p) >= 5:
                out.append({"user": p[0], "pid": p[1], "cpu": p[2], "mem": p[3], "cmd": p[4][:120]})
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
    """Get process logs with improved file detection.

    Strategy priority:
    1. Check if stdout/stderr are redirected to real files (readlink fd/1, fd/2)
    2. Parse cmdline for --output, --log_dir, --save_dir arguments
    3. Search for log files matching PID in script directory
    4. Search for log files matching script name
    5. Fallback to /proc/PID/fd/1 (works if stdout is piped to a file)
    """
    pid_s = str(validate_pid(pid))
    lines = clamp_log_lines(lines)
    info_cmd = (
        "echo __CMDLINE__; cat /proc/" + pid_s + "/cmdline 2>/dev/null | tr '\\0' ' '; echo; "
        "echo __CWD__; readlink /proc/" + pid_s + "/cwd 2>/dev/null; "
        "echo __FD__; ls /proc/" + pid_s + "/fd/ 2>/dev/null | wc -l; "
        "echo __STATUS__; head -10 /proc/" + pid_s + "/status 2>/dev/null; "
        "echo __IO__; cat /proc/" + pid_s + "/io 2>/dev/null; "
        "echo __FDLINKS__; ls -l /proc/" + pid_s + "/fd/ 2>/dev/null | grep -v socket | grep -v pipe | grep -v 'anon_inode' | head -20; "
        "echo __STDOUT__; readlink /proc/" + pid_s + "/fd/1 2>/dev/null; "
        "echo __STDERR__; readlink /proc/" + pid_s + "/fd/2 2>/dev/null; "
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
    stdout_target = sections.get("stdout", "")
    stderr_target = sections.get("stderr", "")

    # ── 1. Check if stdout/stderr redirect to real files ──
    stdout_text = ""
    stderr_text = ""

    def _is_real_file(path):
        """Check if fd target is a real file (not terminal, pipe, socket)."""
        if not path:
            return False
        dev_prefixes = ("/dev/", "pipe:", "socket:", "anon_inode:")
        return not any(path.startswith(p) for p in dev_prefixes)

    if _is_real_file(stdout_target):
        content = _cmd(ssh, "tail -" + str(lines) + " " + quote_remote_path(stdout_target) + " 2>/dev/null", t=8)
        if content and content.strip():
            stdout_text = "[stdout → " + stdout_target + "]\n" + content

    if _is_real_file(stderr_target):
        content = _cmd(ssh, "tail -" + str(lines) + " " + quote_remote_path(stderr_target) + " 2>/dev/null", t=8)
        if content and content.strip():
            stderr_text = "[stderr → " + stderr_target + "]\n" + content

    # ── 2. Extract script info from cmdline ──
    script_path = ""
    script_name = ""
    output_dir = ""

    sm = re.search(r'(\S+\.py)', cmdline)
    if sm:
        raw_path = sm.group(1)
        if raw_path.startswith("/"):
            script_path = raw_path
        elif cwd:
            script_path = cwd.rstrip("/") + "/" + raw_path
        script_name = os.path.basename(script_path).replace(".py", "") if script_path else ""

    # Check for output directory arguments
    for pattern in [
        r'--output[_-]?dir\s+(\S+)', r'--log[_-]?dir\s+(\S+)',
        r'--save[_-]?dir\s+(\S+)', r'--out[_-]?dir\s+(\S+)',
        r'--output\s+(\S+)', r'-o\s+(\S+)',
    ]:
        m = re.search(pattern, cmdline, re.IGNORECASE)
        if m:
            d = m.group(1)
            if d.startswith("/"):
                output_dir = d
            elif cwd:
                output_dir = cwd.rstrip("/") + "/" + d
            break

    # ── 3. Search for log files if no stdout from redirection ──
    if not stdout_text:
        candidates = []
        search_dirs = []
        if output_dir:
            search_dirs.append(output_dir)
        if script_path:
            sd = os.path.dirname(script_path)
            if sd and sd not in search_dirs:
                search_dirs.append(sd)
        if cwd and cwd not in search_dirs:
            search_dirs.append(cwd)

        for sd in search_dirs:
            raw = _cmd(ssh, "find " + quote_remote_path(sd) + " -maxdepth 4 \\( -name '*.log' -o -name '*.txt' -o -name '*.out' \\) -mmin -300 -type f -printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -15", t=8)
            if raw:
                for lf in raw.strip().split("\n"):
                    parts = lf.split(" ", 1)
                    if len(parts) >= 2:
                        path = parts[1].strip()
                        if path not in candidates:
                            candidates.append(path)

        # Priority matching: PID > script_name+mode > script_name > first candidate
        chosen = ""

        # Highest priority: log file containing this PID
        for c in candidates:
            if pid_s in os.path.basename(c):
                chosen = c; break

        # Second: match by script name
        if not chosen and script_name:
            mode = ""
            mm = re.search(r'--mode\s+(\w+)', cmdline)
            if mm:
                mode = mm.group(1)
            for c in candidates:
                bn = os.path.basename(c).lower()
                if script_name.lower() in bn:
                    if mode and mode.lower() in bn:
                        chosen = c; break
            if not chosen:
                for c in candidates:
                    if script_name.lower() in os.path.basename(c).lower():
                        chosen = c; break

        # Third: match by output dir
        if not chosen and output_dir:
            for c in candidates:
                if c.startswith(output_dir):
                    chosen = c; break

        # Fallback: first candidate (most recently modified)
        if not chosen and candidates:
            chosen = candidates[0]

        if chosen:
            content = _cmd(ssh, "tail -" + str(lines) + " " + quote_remote_path(chosen) + " 2>/dev/null", t=8)
            if content and content.strip():
                stdout_text = "[Log file: " + chosen + "]\n" + content

    # ── 4. Fallback: try /proc/PID/fd/1 directly ──
    if not stdout_text:
        stdout_text = _cmd(ssh, "timeout 3 tail -" + str(lines) + " /proc/" + pid_s + "/fd/1 2>/dev/null || echo ''", t=8)

    # ── 5. Check fd links for any log-like open files ──
    if not stdout_text and not stderr_text:
        fdlinks = sections.get("fdlinks", "")
        if fdlinks:
            for line in fdlinks.split("\n"):
                if "->" in line:
                    target = line.split("->")[-1].strip()
                    if target.endswith((".log", ".txt", ".out")) and _is_real_file(target):
                        content = _cmd(ssh, "tail -" + str(lines) + " " + quote_remote_path(target) + " 2>/dev/null", t=8)
                        if content and content.strip():
                            stdout_text = "[fd → " + target + "]\n" + content
                            break

    return {
        "stdout": stdout_text or "",
        "stderr": stderr_text or "",
        "detail": {
            "cmdline": cmdline,
            "cwd": cwd,
            "open_files": sections.get("fd", "0"),
            "status": sections.get("status", ""),
            "io": sections.get("io", ""),
            "stdout_target": stdout_target,
            "stderr_target": stderr_target,
        }
    }


# ── myjobs integration ────────────────────────────────────────
def _memory_to_mb(value):
    """Convert a myjobs memory value to MB without treating it as VRAM."""
    if value is None:
        return 0
    match = re.match(r"^([\d.]+)\s*([KMGT]?)B?$", str(value).strip(), re.IGNORECASE)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).upper()
    scale = {"": 1 / (1024 * 1024), "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    return int(round(amount * scale[unit]))


def _percent_to_number(value):
    """Normalize myjobs CPU text for the existing numeric dashboard field."""
    try:
        number = float(str(value).strip().rstrip("%"))
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return 0


def _user_matches(displayed_user, current_user):
    """Match ps usernames, including the trailing '+' truncation marker."""
    displayed = str(displayed_user or "").strip().casefold()
    current = str(current_user or "").strip().casefold()
    if not displayed or not current:
        return False
    if displayed.endswith("+"):
        return current.startswith(displayed[:-1])
    return displayed == current


def scope_myjobs(myjobs, process_scope="self", fallback_user=None):
    """Restrict myjobs data to the current login user unless all is explicit."""
    if myjobs is None:
        return None
    result = dict(myjobs)
    scope = "all" if str(process_scope or "").strip().lower() == "all" else "self"
    current_user = result.get("current_user") or fallback_user
    result["current_user"] = current_user
    result["process_scope"] = scope
    all_processes = list(result.get("processes", []))
    result["total_visible_processes"] = len(all_processes)
    if scope == "all":
        return result
    result["processes"] = [
        process for process in all_processes
        if _user_matches(process.get("user"), current_user)
    ]
    result["users"] = [
        user for user in result.get("users", [])
        if _user_matches(user.get("user"), current_user)
    ]
    return result


def _format_elapsed(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _format_memory_mb(memory_mb):
    memory_mb = max(0, int(memory_mb or 0))
    if memory_mb >= 1024:
        return f"{memory_mb / 1024:.1f}G"
    return f"{memory_mb}M"


def _task_group(command):
    parts = str(command or "").split()
    if not parts:
        return "other"
    executable = os.path.basename(parts[0]).lower()
    if executable.startswith("python") or executable in {"torchrun", "accelerate", "deepspeed"}:
        for part in parts[1:]:
            if part.endswith(".py"):
                return os.path.basename(part)
    return os.path.basename(parts[0]) or "other"


def _is_own_task(process):
    command = str(process.get("cmd", ""))
    lowered = command.casefold()
    executable = os.path.basename(lowered.split()[0]) if lowered.split() else ""
    if executable in {"nvitop", "nvidia-smi", "htop", "top", "watch", "ps"}:
        return False
    excluded = (
        "sshd:", "sftp-server", ".vscode-server", "ps -u ", "__current_user__",
        "multiprocessing.resource_tracker", "multiprocessing.spawn", "spawn_main",
    )
    if any(marker in lowered for marker in excluded):
        return False
    task_markers = (
        "python", "torchrun", "accelerate", "deepspeed", "jupyter",
        "tensorboard", "train", "finetune", "pretrain", "ray::",
        "wandb", "mlflow",
    )
    if any(marker in lowered for marker in task_markers):
        return True
    if process.get("gpu"):
        return True
    return process.get("rss_mb", 0) >= 200 and process.get("elapsed_seconds", 0) >= 60


def parse_own_tasks_output(raw):
    """Parse native ps/proc data and keep only current-user task processes."""
    if not raw:
        return None
    current_user = None
    current_section = None
    raw_processes = []
    gpu_pids = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("__CURRENT_USER__"):
            current_user = stripped.partition(" ")[2].strip() or None
            continue
        if stripped == "__PROCESSES__":
            current_section = "processes"
            continue
        if stripped == "__GPU_PIDS__":
            current_section = "gpu_pids"
            continue
        if current_section == "gpu_pids":
            if stripped.isdigit():
                gpu_pids.add(stripped)
            continue
        if current_section != "processes":
            continue
        parts = stripped.split(None, 7)
        if len(parts) < 8 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            rss_mb = round(int(parts[5]) / 1024)
            elapsed_seconds = int(parts[6])
        except ValueError:
            continue
        raw_processes.append({
            "pid": parts[0],
            "ppid": parts[1],
            "user": parts[2],
            "cpu_number": _percent_to_number(parts[3]),
            "mem_percent": _percent_to_number(parts[4]),
            "rss_mb": rss_mb,
            "elapsed_seconds": elapsed_seconds,
            "cmd": parts[7],
        })

    if not current_user:
        return None
    for process in raw_processes:
        process["gpu"] = process["pid"] in gpu_pids

    candidates = [process for process in raw_processes if _is_own_task(process)]
    by_pid = {process["pid"]: process for process in candidates}
    tasks = []
    for process in candidates:
        parent = by_pid.get(process["ppid"])
        if parent and parent.get("cmd") == process.get("cmd"):
            continue
        tasks.append(process)

    processes = []
    groups = {}
    for process in tasks:
        group = _task_group(process["cmd"])
        cpu_number = process["cpu_number"]
        processes.append({
            "user": process["user"],
            "pid": process["pid"],
            "ppid": process["ppid"],
            "cpu": f"{cpu_number:g}%",
            "ram": _format_memory_mb(process["rss_mb"]),
            "task": process["cmd"],
            "cmd": process["cmd"],
            "start": "",
            "run": _format_elapsed(process["elapsed_seconds"]),
            "gpu": process["gpu"],
        })
        aggregate = groups.setdefault(group, {"count": 0, "cpu": 0.0, "ram_mb": 0})
        aggregate["count"] += 1
        aggregate["cpu"] += float(cpu_number)
        aggregate["ram_mb"] += process["rss_mb"]

    my_tasks = []
    for group, aggregate in sorted(groups.items(), key=lambda item: -item[1]["cpu"]):
        cpu = aggregate["cpu"]
        my_tasks.append({
            "group": group,
            "count": str(aggregate["count"]),
            "cpu": f"{cpu:g}%",
            "ram": _format_memory_mb(aggregate["ram_mb"]),
        })

    total_cpu = sum(float(process["cpu_number"]) for process in tasks)
    total_ram_mb = sum(process["rss_mb"] for process in tasks)
    users = []
    if processes:
        users.append({
            "user": current_user,
            "procs": str(len(processes)),
            "cpu": f"{total_cpu:g}%",
            "ram": _format_memory_mb(total_ram_mb),
            "tasks": ", ".join(f"{task['group']}×{task['count']}" for task in my_tasks),
        })

    return {
        "gpus": [],
        "users": users,
        "processes": processes,
        "my_tasks": my_tasks,
        "current_user": current_user,
        "scope": "container",
        "process_scope": "self",
        "data_source": "ps-proc",
        "total_visible_processes": len(raw_processes),
        "warnings": [
            "Processes are restricted to the current SSH user",
            "host PID and per-process VRAM are unavailable from this container",
        ],
    }


def own_tasks_info(ssh):
    """Collect current-user tasks directly from ps and /proc, without myjobs."""
    command = (
        'printf "__CURRENT_USER__ %s\\n" "$(id -un)"; '
        'echo __PROCESSES__; '
        'ps -u "$(id -u)" -o pid=,ppid=,user=,pcpu=,pmem=,rss=,etimes=,args= --no-headers; '
        'echo __GPU_PIDS__; '
        'for pid in $(ps -u "$(id -u)" -o pid=); do '
        'if find "/proc/$pid/fd" -maxdepth 1 -type l -lname "/dev/nvidia*" '
        '-print -quit 2>/dev/null | grep -q .; then echo "$pid"; fi; '
        'done'
    )
    return parse_own_tasks_output(_cmd_dash(ssh, command, t=15))


def parse_myjobs_output(raw):
    """Parse myjobs -d output, including container scope metadata."""
    cleaned = _ANSI_RE.sub("", raw or "")
    result = {
        "gpus": [], "users": [], "processes": [], "my_tasks": [],
        "current_user": None, "scope": "container", "data_source": "myjobs",
        "warnings": [
            "PID values are from the visible container namespace",
            "host PID and per-process VRAM are unavailable from this container",
        ],
    }
    current_section = None
    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("=") or stripped.startswith("\u2500"):
            continue

        user_match = re.search(r"\b(?:current\s+)?user\s*[:=]\s*([\w.+-]+)", stripped, re.IGNORECASE)
        if user_match and not stripped.lower().startswith("user procs"):
            result["current_user"] = user_match.group(1)

        lower = stripped.lower()
        if lower.startswith("gpu:"):
            current_section = "gpu"; continue
        if lower.startswith("users:"):
            current_section = "users"; continue
        if lower.startswith("processes:"):
            current_section = "processes"; continue
        if lower.startswith("my tasks:"):
            current_section = "my_tasks"; continue
        if stripped.startswith("\u2713") or stripped.startswith("\u2717"):
            result["summary"] = stripped
            current_section = None; continue

        if current_section == "gpu":
            match = re.search(r"cuda:(\d+)\s+.*?(\d+)/(\d+)M\s+(\d+)%\s+(\d+)M\s+free", stripped, re.IGNORECASE)
            if match:
                result["gpus"].append({
                    "idx": int(match.group(1)), "mem_used": int(match.group(2)),
                    "mem_total": int(match.group(3)), "util": int(match.group(4)),
                    "mem_free": int(match.group(5)),
                })
        elif current_section == "users":
            parts = stripped.split(None, 4)
            if len(parts) >= 5 and parts[0].lower() != "user" and parts[1].isdigit():
                result["users"].append({
                    "user": parts[0], "procs": parts[1], "cpu": parts[2],
                    "ram": parts[3], "tasks": parts[4],
                })
        elif current_section == "processes":
            parts = stripped.split(None, 6)
            if len(parts) >= 5 and parts[0].lower() != "user" and parts[1].isdigit():
                result["processes"].append({
                    "user": parts[0], "pid": parts[1], "cpu": parts[2],
                    "ram": parts[3], "task": parts[4],
                    "start": parts[5] if len(parts) > 5 else "",
                    "run": parts[6] if len(parts) > 6 else "",
                })
        elif current_section == "my_tasks":
            parts = stripped.split(None, 3)
            if len(parts) >= 4 and parts[0].lower() != "group" and parts[1].isdigit():
                result["my_tasks"].append({
                    "group": parts[0], "count": parts[1],
                    "cpu": parts[2], "ram": parts[3],
                })

    if not any(result[key] for key in ("gpus", "users", "processes", "my_tasks")):
        return None
    return result


def training_from_myjobs(myjobs, gpu_processes=None):
    """Convert visible container processes without inventing host/GPU fields."""
    myjobs = scope_myjobs(
        myjobs,
        (myjobs or {}).get("process_scope", "self"),
        (myjobs or {}).get("current_user"),
    )
    gpu_by_pid = {str(p.get("pid")): p for p in (gpu_processes or [])}
    training = []
    for process in (myjobs or {}).get("processes", []):
        raw_pid = process.get("pid", "")
        pid = int(raw_pid) if str(raw_pid).isdigit() else raw_pid
        record = {
            "pid": pid,
            "container_pid": pid,
            "host_pid": None,
            "pid_scope": "container",
            "data_source": (myjobs or {}).get("data_source", "myjobs"),
            "user": process.get("user", ""),
            "cmd": process.get("cmd") or process.get("task", ""),
            "start": process.get("start", ""),
            "run": process.get("run", ""),
            "elapsed": process.get("run") or process.get("start", ""),
            "cpu": _percent_to_number(process.get("cpu", "0")),
            "ram_mb": _memory_to_mb(process.get("ram")),
            "vram_mb": None,
            "vram": None,
            "gpu": bool(process.get("gpu")),
        }
        matched_gpu = gpu_by_pid.get(str(pid))
        if matched_gpu:
            record["vram_mb"] = matched_gpu.get("vram")
            record["vram"] = matched_gpu.get("vram")
            record["vram_source"] = "nvidia-smi"
        training.append(record)
    return training


def myjobs_info(ssh):
    """Run myjobs -d and parse output into structured data."""
    raw = _cmd_dash(ssh, "myjobs -d 2>/dev/null", t=15)
    if not raw or "not found" in raw.lower() or "no such file" in raw.lower():
        return None
    result = parse_myjobs_output(raw)
    if result is not None:
        result["raw"] = raw
    return result


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
        mj = own_tasks_info(ssh)
        if mj is None:
            mj = scope_myjobs(
                myjobs_info(ssh),
                "self",
                srv.get("username", "root"),
            )

        # Training cards are always limited to the current SSH user.  The native
        # ps/proc collector is primary; nvidia-smi data is merged only when the
        # PID is visible in the same namespace.
        current_user = (mj or {}).get("current_user") or srv.get("username", "root")
        native_training = [
            process for process in t
            if _user_matches(process.get("user"), current_user)
        ]
        user_training = training_from_myjobs(mj, native_training)
        user_pids = {str(process.get("pid")) for process in user_training}
        for process in native_training:
            if str(process.get("pid")) not in user_pids:
                user_training.append(process)
        t = user_training

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

        if path.startswith("/api/") and not request_is_authorized(self.headers, dashboard_security):
            self.send_json({"error": "unauthorized"}, 401)
            return

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
            try:
                pid, lines = parse_log_request(qs)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
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
                log_data = get_process_log(ssh, pid, lines)
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
    global config, poll_interval, _available_servers, dashboard_security

    pa = argparse.ArgumentParser()
    pa.add_argument("--port", type=int, default=8765)
    pa.add_argument("--server", "-s")
    pa.add_argument("--no-browser", action="store_true")
    pa.add_argument("--interval", type=int, default=10)
    pa.add_argument("--bind", default="127.0.0.1", help="Listen address (loopback by default)")
    pa.add_argument("--allow-remote", action="store_true", help="Allow non-loopback binding; requires --token")
    pa.add_argument("--token", help="Bearer token required for remote API access")
    a = pa.parse_args()

    try:
        dashboard_security = dashboard_settings(a.bind, a.allow_remote, a.token)
    except ValueError as exc:
        pa.error(str(exc))

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

    s = ThreadingHTTPServer((dashboard_security.bind, a.port), H)
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
