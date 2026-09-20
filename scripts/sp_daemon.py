#!/usr/bin/env python3
"""SSH 连接池守护进程（惰性连接，按需建立，空闲自动释放）。

设计要点:
  - 启动时不建立任何连接；只有收到对某台服务器的 exec 请求时才连接该服务器。
  - 每台服务器一条连接，带独立锁（同一服务器的命令串行执行，避免通道互相干扰）。
  - 空闲释放: 单条连接超过 CONN_IDLE 未使用自动断开；全部连接断开/从未建立且
    DAEMON_EXIT_IDLE 内无任何请求，进程自动退出（零常驻开销），下次调用时由
    security.ensure_daemon() 自动拉起。
  - 通信: 127.0.0.1 随机端口 + 一次性随机 token（存于 state/daemon.json）。
    仅本机、需 token，拒绝来源不明的请求。
  - 守护进程故障不影响可用性：调用方（security.py）检测不到守护进程时
    回退为直连。

协议（每行一个 JSON）:
  {"op":"ping"}                                   -> {"ok":true,...}
  {"op":"status"}                                 -> 各连接状态
  {"op":"exec","server":"pollux","cmd":"...",     -> {"ok":true,"rc":0,
     "read_timeout":30,"get_pty":false}              "stdout":"...","stderr":"..."}
  {"op":"close","server":"pollux"}                -> 断开并重连该连接
"""
from __future__ import annotations

import json
import os
import secrets
import socketserver
import sys
import threading
import time
from pathlib import Path

import security

SCRIPT_DIR = Path(__file__).resolve().parent
# 状态目录与 security.py 共用（env SP_POOL_STATE > scripts/local_config.json > 默认）
STATE_DIR = security.DAEMON_STATE_PATH.parent
DAEMON_STATE = security.DAEMON_STATE_PATH
LOG_FILE = STATE_DIR / "daemon.log"

# 单连接空闲超时（秒）：超时后断开该服务器的连接
CONN_IDLE = float(os.environ.get("SP_DAEMON_CONN_IDLE", 30 * 60))
# 无连接且无活动的进程退出时间（秒）
DAEMON_EXIT_IDLE = float(os.environ.get("SP_DAEMON_EXIT_IDLE", 30 * 60))
GC_INTERVAL = 60


def log(msg: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def load_servers() -> dict[str, dict]:
    cfg = security.load_server_config()
    defaults = cfg.get("defaults", {}) or {}
    result: dict[str, dict] = {}
    if isinstance(cfg.get("servers"), dict):
        items = cfg["servers"].items()
    else:
        items = [("default", cfg)]
    for alias, s in items:
        merged = {**defaults, **(s or {})}
        result[alias] = {
            "host": merged.get("host"),
            "port": merged.get("port", 22),
            "username": merged.get("username", "root"),
            "password": merged.get("password"),
            "key_file": merged.get("key_file"),
            "host_key_policy": merged.get("host_key_policy"),
        }
    return result


class ConnectionPool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: dict[str, dict] = {}  # alias -> {"client", "last_used"}
        self.last_activity = time.time()

    def _connect(self, alias: str, servers: dict[str, dict]):
        cfg = servers.get(alias)
        if not cfg or not cfg.get("host"):
            raise RuntimeError(f"server_config.json 中未找到服务器 '{alias}'")
        log(f"connect {alias} ({cfg['host']})")
        return security.connect_ssh(
            cfg["host"], int(cfg["port"]), cfg["username"],
            password=cfg.get("password"), key_file=cfg.get("key_file"),
            host_key_policy=cfg.get("host_key_policy"),
        )

    def get(self, alias: str):
        with self._lock:
            entry = self._conns.get(alias)
            if entry:
                client = entry["client"]
                transport = client.get_transport()
                if transport is None or not transport.is_active():
                    log(f"{alias}: 连接已失效，重连")
                    self._drop(alias)
                    entry = None
            if entry is None:
                client = self._connect(alias, self.servers)
                entry = {"client": client, "last_used": time.time()}
                self._conns[alias] = entry
            entry["last_used"] = time.time()
            return entry

    def exec(self, alias: str, cmd: str, read_timeout, get_pty: bool):
        # 同一连接上并发执行：每个 exec_remote 走自己的 channel，
        # paramiko transport 支持并发 channel（sshd 默认 MaxSessions=10），
        # 不需要串行锁，长命令不会阻塞同一服务器的其他命令。
        entry = self.get(alias)
        entry["last_used"] = time.time()
        try:
            stdout, stderr, rc = security.exec_remote(
                entry["client"], cmd,
                read_timeout=read_timeout, get_pty=get_pty,
            )
            entry["last_used"] = time.time()
            return {"ok": True, "rc": rc, "stdout": stdout, "stderr": stderr}
        except Exception as exc:  # noqa: BLE001 — 上报给客户端由它裁决
            entry["last_used"] = time.time()
            return {"ok": False,
                    "error": type(exc).__name__ + ": " + str(exc)}

    def close(self, alias: str) -> bool:
        with self._lock:
            return self._drop(alias)

    def _drop(self, alias: str) -> bool:
        entry = self._conns.pop(alias, None)
        if not entry:
            return False
        try:
            entry["client"].close()
        except Exception:  # noqa: BLE001
            pass
        log(f"{alias}: 连接已断开")
        return True

    def gc(self) -> None:
        """断开空闲超时的连接；无连接且无活动则退出进程。"""
        now = time.time()
        idle_aliases = [a for a, e in list(self._conns.items())
                        if now - e["last_used"] > CONN_IDLE]
        for alias in idle_aliases:
            with self._lock:
                self._drop(alias)
            log(f"{alias}: 空闲超过 {max(CONN_IDLE/60, 0.1):.1f} 分钟，自动断开")
        if not self._conns and now - self.last_activity > DAEMON_EXIT_IDLE:
            log(f"守护进程空闲超过 {max(DAEMON_EXIT_IDLE/60, 0.1):.1f} 分钟，自动退出")
            cleanup_and_exit(0)

    def status(self) -> dict:
        now = time.time()
        return {
            "connections": {
                a: {"idle_seconds": round(now - e["last_used"], 1)}
                for a, e in self._conns.items()
            },
            "idle_exit_in": max(0, round(DAEMON_EXIT_IDLE -
                                         (now - self.last_activity), 1))
            if not self._conns else None,
        }


POOL = ConnectionPool()
POOL.servers = {}
TOKEN = ""


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(64 * 1024 * 1024)
        try:
            req = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            self._reply({"ok": False, "error": "invalid json"})
            return
        if req.get("token") != TOKEN:
            self._reply({"ok": False, "error": "unauthorized"})
            return
        POOL.last_activity = time.time()
        op = req.get("op")
        try:
            if op == "ping":
                self._reply({"ok": True, "pid": os.getpid()})
            elif op == "status":
                self._reply({"ok": True, **POOL.status()})
            elif op == "exec":
                result = POOL.exec(
                    str(req.get("server", "")),
                    str(req.get("cmd", "")),
                    req.get("read_timeout"),
                    bool(req.get("get_pty")),
                )
                self._reply(result)
            elif op == "close":
                self._reply({"ok": True,
                             "closed": POOL.close(str(req.get("server", "")))})
            else:
                self._reply({"ok": False, "error": f"unknown op: {op}"})
        except Exception as exc:  # noqa: BLE001
            self._reply({"ok": False,
                         "error": type(exc).__name__ + ": " + str(exc)})

    def _reply(self, payload: dict) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(data)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def cleanup_and_exit(code: int) -> None:
    try:
        DAEMON_STATE.unlink(missing_ok=True)
    except OSError:
        pass
    os._exit(code)


def gc_loop() -> None:
    while True:
        time.sleep(GC_INTERVAL)
        try:
            POOL.gc()
        except Exception as exc:  # noqa: BLE001
            log(f"gc error: {exc!r}")


def main() -> None:
    global TOKEN
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    POOL.servers = load_servers()
    TOKEN = secrets.token_hex(16)

    server = Server(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    # 原子写入：先写临时文件再 rename，避免并发读方拿到半份 JSON
    tmp_state = DAEMON_STATE.with_suffix(".tmp")
    tmp_state.write_text(json.dumps({
        "port": port, "token": TOKEN, "pid": os.getpid(),
        "started": time.time(),
    }), encoding="utf-8")
    os.replace(tmp_state, DAEMON_STATE)
    log(f"daemon started pid={os.getpid()} port={port} "
        f"servers={list(POOL.servers)}")

    threading.Thread(target=gc_loop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        cleanup_and_exit(0)


if __name__ == "__main__":
    main()
