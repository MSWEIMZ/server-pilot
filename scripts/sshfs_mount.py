#!/usr/bin/env python3
"""SSHFS 挂载自动管理（rclone mount，多服务器）。

子命令:
  ensure [--server NAME]   挂载指定服务器（未挂载则启动），刷新其心跳，
                           确保空闲监视器在运行。skill 每次使用时的入口（幂等）。
  status                   打印所有已知服务器的挂载/心跳状态。
  unmount [--server NAME | --all]  卸载指定服务器（或全部 + 停监视器）。
  watch                    空闲监视器循环（内部使用，ensure 会自动拉起）。

设计:
  - 服务器信息来自同目录 server_config.json（多服务器配置）。
  - rclone remote 与服务器别名同名，缺失时自动创建于 rclone 配置文件
    （local_config.json 的 rclone_config 或默认 ~/.config/rclone/rclone.conf）。
  - 每台服务器固定分配一个盘符，映射持久化在 state/drives.json。
  - 每台服务器独立心跳/mounter pid (state/heartbeat.<alias>, mounter.<alias>.pid)。
  - 单一监视器进程统一管理：哪台空闲超时就卸载哪台，互不影响。
  - 缓存在每台服务器独立目录 <cache_root>/<alias>（sshfs_cache_dir 可配置）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---- 配置（不含任何机器特定路径：env 变量 > scripts/local_config.json > 通用默认） ----
import security  # noqa: E402  (路径解析共用 local_setting)

SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_CONF = SCRIPT_DIR / "server_config.json"
RCLONE = security.local_setting("rclone_path", "SP_RCLONE", "rclone")
RCLONE_CONF = security.local_setting(
    "rclone_config", "SP_RCLONE_CONFIG",
    os.path.expanduser("~/.config/rclone/rclone.conf"))
KNOWN_HOSTS = security.local_setting(
    "known_hosts_file", "SP_KNOWN_HOSTS",
    os.path.expanduser("~/.ssh/known_hosts"))
CACHE_ROOT = Path(security.local_setting(
    "sshfs_cache_dir", "SP_SSHFS_CACHE",
    os.path.expanduser("~/.server-pilot/rclone-cache")))
STATE_DIR = CACHE_ROOT / "state"
DRIVES_FILE = STATE_DIR / "drives.json"
WATCHER_PID = STATE_DIR / "watcher.pid"
IDLE_MINUTES_DEFAULT = 45  # 某台服务器超过该时间无 skill 活动则自动卸载
WATCH_INTERVAL = 60        # 监视器检查间隔（秒）
# 盘符候选顺序（跳过 C/D/E 系统盘）
DRIVE_CANDIDATES = ["P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
                    "A", "B", "F", "G", "H", "I", "J", "K", "L", "M", "N"]

CREATE_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | \
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | \
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ---- 配置读取 ----

def load_servers() -> dict[str, dict]:
    """从 server_config.json 读取 {alias: {host, port, user, key_file}}。"""
    try:
        cfg = json.loads(SERVER_CONF.read_text(encoding="utf-8"))
    except OSError:
        return {}
    defaults = cfg.get("defaults", {}) or {}
    if isinstance(cfg.get("servers"), dict):
        out = {}
        for alias, s in cfg["servers"].items():
            merged = {**defaults, **(s or {})}
            out[alias] = {
                "host": merged.get("host"),
                "port": merged.get("port", 22),
                "user": merged.get("username", "root"),
                "key_file": merged.get("key_file"),
            }
        return out
    return {"default": {
        "host": cfg.get("host"),
        "port": cfg.get("port", 22),
        "user": cfg.get("username", "root"),
        "key_file": cfg.get("key_file"),
    }}


# ---- 状态文件 ----

def _state_setup() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _heartbeat_file(alias: str) -> Path:
    return STATE_DIR / f"heartbeat.{alias}.txt"


def _mounter_pid_file(alias: str) -> Path:
    return STATE_DIR / f"mounter.{alias}.pid"


def touch_heartbeat(alias: str) -> None:
    _state_setup()
    _heartbeat_file(alias).write_text(str(time.time()))


def heartbeat_age(alias: str) -> float:
    try:
        return time.time() - float(_heartbeat_file(alias).read_text().strip())
    except (OSError, ValueError):
        return float("inf")


def load_drives() -> dict[str, str]:
    _state_setup()
    try:
        return json.loads(DRIVES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_drives(drives: dict[str, str]) -> None:
    _state_setup()
    DRIVES_FILE.write_text(json.dumps(drives, indent=1), encoding="utf-8")


def pick_drive(drives: dict[str, str]) -> str:
    used = {d.rstrip(":") for d in drives.values()}
    for c in DRIVE_CANDIDATES:
        if c not in used and not os.path.isdir(f"{c}:\\"):
            return c
    raise RuntimeError("没有可用的盘符")


# ---- 进程辅助 ----

def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        capture_output=True, text=True,
    ).stdout
    return f'"{pid}"' in out


def mounter_pid(alias: str) -> int | None:
    pid = _read_pid(_mounter_pid_file(alias))
    if pid and _pid_alive(pid):
        return pid
    return None


def _iter_mount_procs() -> list[tuple[int, str]]:
    """列出所有 rclone 进程: [(pid, cmdline), ...]"""
    try:
        rows = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='rclone.exe'\" "
             "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True,
        ).stdout
    except OSError:
        return []
    try:
        data = json.loads(rows)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [(int(p["ProcessId"]), p.get("CommandLine") or "") for p in data]


def _find_mounter_pid(alias: str, drive: str) -> int | None:
    """在 rclone mount 进程中按 remote 名和盘符精确定位（多挂载不会串）。"""
    needle_remote = f"{alias}:"
    needle_drive = f"{drive.rstrip(':')}:"
    for pid, cmd in _iter_mount_procs():
        if "mount" in cmd and needle_remote in cmd and needle_drive in cmd:
            return pid
    return None


def _find_existing_mount(alias: str) -> tuple[int, str] | None:
    """从已有挂载进程的命令行解析盘符，用于接管/迁移旧状态。返回 (pid, 'X:')。"""
    for pid, cmd in _iter_mount_procs():
        if "mount" not in cmd or f"{alias}:" not in cmd:
            continue
        for tok in cmd.split():
            if len(tok) == 2 and tok[1] == ":" and tok[0].isalpha():
                return pid, tok.upper()
    return None


def kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


# ---- rclone remote ----

def ensure_rclone_remote(alias: str, cfg: dict) -> bool:
    """确保 rclone.conf 里有该服务器的 remote，返回是否可用。"""
    out = subprocess.run(
        [RCLONE, "listremotes", "--config", RCLONE_CONF],
        capture_output=True, text=True,
    ).stdout
    if f"{alias}:" in out:
        return True
    if not cfg.get("host"):
        return False
    args = [
        RCLONE, "config", "create", alias, "sftp",
        f"host={cfg['host']}",
        f"port={cfg.get('port', 22)}",
        f"user={cfg.get('user', 'root')}",
        "--config", RCLONE_CONF,
    ]
    if cfg.get("key_file"):
        args.insert(-2, f"key_file={cfg['key_file']}")
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        return False
    subprocess.run(
        [RCLONE, "config", "update", alias,
         f"known_hosts_file={KNOWN_HOSTS}", "--config", RCLONE_CONF],
        capture_output=True,
    )
    return True


# ---- 挂载核心 ----

def drive_accessible(drive: str) -> bool:
    return os.path.isdir(drive if drive.endswith("\\") else drive + "\\")


def start_mount(alias: str, drive: str) -> bool:
    _state_setup()
    cmd = [
        RCLONE, "mount", f"{alias}:", drive,
        "--config", RCLONE_CONF,
        "--vfs-cache-mode", "writes",
        "--cache-dir", str(CACHE_ROOT / alias),
        "--vfs-cache-max-size", "4G",
        "--log-file", str(CACHE_ROOT / f"mount.{alias}.log"),
        "--log-level", "INFO",
    ]
    proc = subprocess.Popen(cmd, creationflags=CREATE_FLAGS,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _mounter_pid_file(alias).write_text(str(proc.pid))
    for _ in range(30):  # rclone 有连接超时，最多等 30s
        if drive_accessible(drive):
            return True
        if proc.poll() is not None:
            break
        time.sleep(1)
    # 超时或进程退出：清理失败残留
    if proc.poll() is None:
        kill_pid(proc.pid)
    _mounter_pid_file(alias).unlink(missing_ok=True)
    return False


def unmount_server(alias: str) -> bool:
    drives = load_drives()
    drive = drives.get(alias, "")
    pid = mounter_pid(alias) or (
        _find_mounter_pid(alias, drive) if drive else None)
    did = False
    if pid:
        kill_pid(pid)
        did = True
    _mounter_pid_file(alias).unlink(missing_ok=True)
    if drive and drive in drives.values():
        drives.pop(alias, None)
        save_drives(drives)
    return did


def _watcher_pid() -> int | None:
    pid = _read_pid(WATCHER_PID)
    if pid and _pid_alive(pid):
        return pid
    return None


def _start_watcher() -> None:
    _state_setup()
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "watch"],
        creationflags=CREATE_FLAGS,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    WATCHER_PID.write_text(str(proc.pid))


def ensure(alias: str, servers: dict[str, dict]) -> int:
    touch_heartbeat(alias)
    cfg = servers.get(alias)
    if not cfg:
        print(f"ERROR: server_config.json 中未找到服务器 '{alias}'")
        return 2
    if not ensure_rclone_remote(alias, cfg):
        print(f"ERROR: 无法为 {alias} 创建 rclone remote（配置不完整?)")
        return 2

    drives = load_drives()
    drive = drives.get(alias)
    if drive and drive_accessible(drive):
        if not mounter_pid(alias):
            pid = _find_mounter_pid(alias, drive)
            if pid:
                _mounter_pid_file(alias).write_text(str(pid))
        print(f"挂载正常: {drive} -> {alias}: （{cfg['host']}）")
    else:
        if not drive:
            # 无盘符映射：先尝试接管已存在的挂载进程（旧版状态迁移）
            existing = _find_existing_mount(alias)
            if existing:
                pid, drive = existing
                drives[alias] = drive
                save_drives(drives)
                _mounter_pid_file(alias).write_text(str(pid))
                print(f"接管现有挂载: {drive} -> {alias}: （{cfg['host']}）")
                if not _watcher_pid():
                    _start_watcher()
                    print("空闲监视器已启动")
                return 0
            drive = pick_drive(drives) + ":"
            drives[alias] = drive
            save_drives(drives)
        if not start_mount(alias, drive):
            # 挂载失败（服务器不可达/认证失败）：释放盘符映射
            drives.pop(alias, None)
            save_drives(drives)
            print(f"ERROR: {alias} 挂载失败（服务器不可达或认证失败），"
                  f"日志: {CACHE_ROOT}\\mount.{alias}.log")
            return 1
        print(f"已挂载 {alias}: -> {drive} （{cfg['host']}）")

    if not _watcher_pid():
        _start_watcher()
        print("空闲监视器已启动")
    return 0


def watch(idle_minutes: float, check_once: bool = False) -> None:
    servers = load_servers()
    while True:
        for hf in sorted(STATE_DIR.glob("heartbeat.*.txt")):
            alias = hf.stem[len("heartbeat."):]
            drives = load_drives()
            if alias not in drives:
                continue  # 已被卸载
            age = heartbeat_age(alias)
            if age > idle_minutes * 60:
                print(f"{alias} 空闲超过 {idle_minutes} 分钟，自动卸载 "
                      f"{drives.get(alias, '?')}")
                if unmount_server(alias):
                    print(f"{alias}: 已卸载")
        if check_once:
            return
        # 没有任何已挂载服务器时退出监视器
        if not load_drives():
            WATCHER_PID.unlink(missing_ok=True)
            return
        time.sleep(WATCH_INTERVAL)


def status() -> None:
    servers = load_servers()
    drives = load_drives()
    wpid = _watcher_pid()
    print(f"监视器: {'运行中 (pid %d)' % wpid if wpid else '未运行'}")
    print(f"空闲超时: {IDLE_MINUTES_DEFAULT} 分钟（watch --idle 可覆盖）")
    for alias, cfg in servers.items():
        drive = drives.get(alias)
        age = heartbeat_age(alias)
        age_s = "%.1f 分钟前" % (age / 60) if age != float("inf") else "无记录"
        if drive and drive_accessible(drive):
            state = f"已挂载 {drive}"
        elif drive:
            state = f"盘符映射 {drive} 但不可访问(异常)"
        else:
            state = "未挂载"
        print(f"  {alias:<10} {cfg.get('host', '?'):<26} {state}"
              f"  (上次活动: {age_s})")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    cmd = args[0]

    def _opt(name, default=None):
        if name in args:
            i = args.index(name)
            return args[i + 1] if i + 1 < len(args) else True
        return default

    server = _opt("--server", "pollux")
    servers = load_servers()

    if cmd == "ensure":
        sys.exit(ensure(str(server), servers))
    elif cmd == "status":
        status()
    elif cmd == "unmount":
        if "--all" in args:
            for alias in list(load_drives()):
                if unmount_server(alias):
                    print(f"{alias}: 已卸载")
            wpid = _watcher_pid()
            if wpid:
                kill_pid(wpid)
            WATCHER_PID.unlink(missing_ok=True)
            print("全部卸载完成，监视器已停止")
        else:
            if unmount_server(str(server)):
                print(f"{server} 已卸载")
            else:
                print(f"{server} 当前无挂载")
    elif cmd == "watch":
        idle = float(_opt("--idle", IDLE_MINUTES_DEFAULT))
        watch(idle, check_once="--once" in args)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
