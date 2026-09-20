"""Shared local security helpers for Server Pilot scripts."""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path


MAX_LOG_LINES = 1000


DEFAULT_CONNECT_TIMEOUT = 15
#: Bounded client-side read timeout for short status probes. Long enough to
#: survive a heavily loaded server, short enough to fail loudly.
DEFAULT_PROBE_TIMEOUT = 30
READ_TIMEOUT_ENV = "SP_READ_TIMEOUT"
_CHANNEL_CHUNK = 65536


def describe_error(exc) -> str:
    """Return a never-empty, human-readable description of an exception.

    socket.timeout and several paramiko errors stringify to "", which used to
    surface as a bare "SSH Error: " with no diagnostic value.
    """
    message = str(exc).strip()
    name = type(exc).__name__
    if message:
        return name + ": " + message
    return name + ": " + repr(exc)


def normalize_read_timeout(value):
    """Normalize a channel read timeout into seconds or None.

    None, empty strings, none/off/unlimited and any value <= 0 mean NO
    TIMEOUT. This is deliberately distinct from the SSH connect timeout.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "none", "off", "unlimited", "no", "false"}:
            return None
        value = text
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a number of seconds or 'none'") from exc
    if seconds != seconds or seconds <= 0:
        return None
    return seconds


def resolve_read_timeout(value=None, env_var=READ_TIMEOUT_ENV):
    """Resolve a read timeout; SP_READ_TIMEOUT overrides value when set."""
    env_value = os.environ.get(env_var)
    if env_value is not None and env_value.strip() != "":
        return normalize_read_timeout(env_value)
    return normalize_read_timeout(value)


def exec_remote(ssh, command, read_timeout=None, get_pty=False):
    """Run command and return (stdout, stderr, exit_code) as text.

    read_timeout bounds how long the client waits for channel data and is
    unlimited by default, so long-running remote commands are not killed by a
    short client-side read timeout. stdout and stderr are drained concurrently
    so a command that fills the stderr window cannot deadlock.
    """
    timeout = resolve_read_timeout(read_timeout)
    _stdin, stdout, stderr = ssh.exec_command(
        command, timeout=timeout, get_pty=get_pty
    )
    channel = stdout.channel
    out_chunks, err_chunks = [], []
    # ``timeout`` bounds *idleness*, not total wall time: every chunk of
    # stdout/stderr restarts the clock, so a long but chatty command (a
    # build log, a test runner) is never cut off by its own duration.
    last_data = time.monotonic()

    while True:
        got_data = False
        if channel.recv_ready():
            out_chunks.append(channel.recv(_CHANNEL_CHUNK))
            got_data = True
        if channel.recv_stderr_ready():
            err_chunks.append(channel.recv_stderr(_CHANNEL_CHUNK))
            got_data = True
        if got_data:
            last_data = time.monotonic()
            continue
        if channel.exit_status_ready():
            break
        if timeout is not None and time.monotonic() - last_data >= timeout:
            raise TimeoutError(
                "no channel data from remote command for %gs" % timeout
            )
        time.sleep(0.02)

    grace_end = time.monotonic() + 0.5
    while time.monotonic() < grace_end:
        if channel.recv_ready():
            out_chunks.append(channel.recv(_CHANNEL_CHUNK))
        elif channel.recv_stderr_ready():
            err_chunks.append(channel.recv_stderr(_CHANNEL_CHUNK))
        else:
            break

    # A dropped transport closes the channel without ever delivering an exit
    # status. paramiko then returns -1 from recv_exit_status(), which used to
    # look exactly like a command that legitimately exited with -1: callers
    # saw truncated output and no error. Only treat this as an interruption
    # when the transport is actually gone, so servers that simply never send
    # an exit status keep their previous -1 behaviour.
    status_event = getattr(channel, "status_event", None)
    got_status = status_event.is_set() if status_event is not None else True
    transport = getattr(ssh, "get_transport", None)
    transport = transport() if callable(transport) else None
    transport_dead = transport is None or not transport.is_active()
    if not got_status and transport_dead:
        raise ConnectionError(
            "SSH connection was interrupted before the remote command "
            "returned an exit status; output above is partial (%d bytes)"
            % sum(len(c) for c in out_chunks)
        )

    exit_code = channel.recv_exit_status()
    return (
        b"".join(out_chunks).decode("utf-8", errors="replace"),
        b"".join(err_chunks).decode("utf-8", errors="replace"),
        exit_code,
    )




def require_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError(
            "paramiko is required. Install it in the active environment with: "
            "python -m pip install paramiko"
        ) from exc
    return paramiko


def project_known_hosts_path() -> Path:
    return Path(__file__).with_name("known_hosts")


def normalize_host_key_policy(value) -> str:
    policy = "strict" if value in (None, "") else str(value).lower()
    if policy not in {"accept-new", "strict"}:
        raise ValueError("host_key_policy must be accept-new or strict")
    return policy


def _accept_new_policy(paramiko):
    known_hosts = project_known_hosts_path()

    class AcceptNewPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            client._host_keys.add(hostname, key.get_name(), key)
            client._host_keys.save(str(known_hosts))

    return AcceptNewPolicy()


def configure_host_key_policy(client, paramiko, host_key_policy=None) -> None:
    client.load_system_host_keys()
    known_hosts = project_known_hosts_path()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    policy = normalize_host_key_policy(host_key_policy)
    if policy == "accept-new":
        client.set_missing_host_key_policy(_accept_new_policy(paramiko))
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())


def _key_filename(key_file: str | None, include_defaults=True) -> str | None:
    if key_file:
        path = os.path.expanduser(key_file)
        if os.path.exists(path):
            return path
    if not include_defaults:
        return None
    for candidate in ("~/.ssh/id_ed25519", "~/.ssh/id_ecdsa", "~/.ssh/id_rsa"):
        path = os.path.expanduser(candidate)
        if os.path.exists(path):
            return path
    return None


def connect_ssh(host, port, username, password=None, key_file=None, timeout=15, retries=3, host_key_policy=None):
    """Connect using the configured host-key policy; default to strict verification."""
    paramiko = require_paramiko()
    for attempt in range(retries):
        client = paramiko.SSHClient()
        try:
            configure_host_key_policy(client, paramiko, host_key_policy)
            kwargs = {"hostname": host, "port": int(port), "username": username, "timeout": timeout}
            key = _key_filename(key_file, include_defaults=False)
            if key:
                kwargs["key_filename"] = key
            elif password:
                kwargs["password"] = password
            else:
                key = _key_filename(None)
                if key:
                    kwargs["key_filename"] = key
                else:
                    raise RuntimeError("No SSH key or password available for this server.")
            client.connect(**kwargs)
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(15)
            return client
        except Exception:
            client.close()
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def validate_pid(value) -> int:
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("pid must be a positive decimal integer")
    return int(text)


def clamp_log_lines(value, minimum=1, maximum=MAX_LOG_LINES) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError) as exc:
        raise ValueError("lines must be an integer") from exc


def local_settings() -> dict:
    """Load scripts/local_config.json (gitignored, per-machine overrides)."""
    import json

    path = Path(__file__).with_name("local_config.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def local_setting(key: str, env_var: str, default: str):
    """Per-machine setting resolution: environment variable, then
    scripts/local_config.json, then the built-in generic default. Use this
    for anything machine-specific (absolute paths, tool locations) so the
    repo carries no user/machine information."""
    value = os.environ.get(env_var)
    if value:
        return value
    return local_settings().get(key, default)


_DAEMON_STATE_DEFAULT = os.path.expanduser("~/.server-pilot/state/daemon.json")
DAEMON_STATE_PATH = Path(local_setting(
    "pool_state_file", "SP_POOL_STATE", _DAEMON_STATE_DEFAULT))


def _daemon_spawn():
    """Detached-start sp_daemon.py (sits next to this file)."""
    import subprocess
    script = Path(__file__).with_name("sp_daemon.py")
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    subprocess.Popen(
        [os.sys.executable, str(script)],
        creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def daemon_request(payload: dict, deadline: float | None = 30.0):
    """Send one request to the pool daemon.

    Returns the response dict, or None **only** when the daemon was
    unavailable before a single byte of the request was sent — in that case
    callers may safely fall back to a direct SSH connection.

    Raises:
        TimeoutError: the daemon accepted the request but no reply arrived
            within *deadline* seconds. The remote command may STILL be
            running; callers must NOT retry the same command via another
            path or it may execute twice. Pass timeout=None to wait forever
            (same semantics as an unlimited SSH read).
        ConnectionError: the connection broke after the request was sent
            (daemon crashed mid-request). Same no-retry rule applies.
    """
    import json
    import socket

    if os.environ.get("SP_POOL", "").lower() in {"off", "0", "false", "no"}:
        return None
    try:
        state = json.loads(DAEMON_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    for attempt in range(2):
        if state is None:
            _daemon_spawn()
            # wait for state file to appear
            for _ in range(50):
                try:
                    state = json.loads(DAEMON_STATE_PATH.read_text(encoding="utf-8"))
                    break
                except (OSError, ValueError):
                    time.sleep(0.2)
            if state is None:
                return None
        # Connecting still can't have delivered the request yet: any failure
        # here means the request definitely never ran -> retry allowed.
        try:
            sock = socket.create_connection(
                ("127.0.0.1", int(state["port"])),
                timeout=min(deadline, 30.0) if deadline else 30.0,
            )
        except OSError:
            # daemon dead / stale state file: drop state and respawn once
            try:
                DAEMON_STATE_PATH.unlink(missing_ok=True)
            except OSError:
                pass
            state = None
            continue
        # Connected. From here on a failure means the request may already be
        # executing server-side — surface it, never silently retry.
        try:
            sock.settimeout(deadline)
            sock.sendall((json.dumps(dict(payload, token=state["token"])) + "\n")
                         .encode("utf-8"))
            line = sock.makefile("rb").readline()
        except socket.timeout as exc:
            sock.close()
            raise TimeoutError(
                "pool daemon did not reply within %ss; the remote command "
                "may still be running. Do not re-issue the same command "
                "blindly. Use SP_POOL=off to bypass the pool."
                % (deadline,)
            ) from exc
        except OSError as exc:
            sock.close()
            raise ConnectionError(
                "pool daemon connection broke after the request was sent: "
                + describe_error(exc)
            ) from exc
        sock.close()
        if not line:
            raise ConnectionError("pool daemon closed the connection without a reply")
        try:
            return json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise ConnectionError("garbled reply from pool daemon") from exc
    return None


def pooled_exec(alias: str, command: str, read_timeout=None, get_pty=False):
    """Run *command* on server *alias* through the connection pool daemon.

    Returns (stdout, stderr, exit_code). Returns None when the daemon is
    unavailable — callers may then fall back to direct connect_ssh.
    Raises RuntimeError with the daemon-reported error when the daemon itself
    executed but the command failed to run (e.g. server unreachable), and
    TimeoutError when the daemon accepted the request but stayed silent past
    the read timeout. Both mean the request DID reach the server — a caller
    must not transparently re-run the command, or it may execute twice.
    """
    # The daemon applies read_timeout itself (idle-based); the socket deadline
    # only bounds waiting for that reply, with headroom for the daemon to
    # compose and return its own timeout error first.
    deadline = None if read_timeout is None else float(read_timeout) + 60.0
    result = daemon_request(
        {"op": "exec", "server": alias, "cmd": command,
         "read_timeout": read_timeout, "get_pty": bool(get_pty)},
        deadline=deadline,
    )
    if result is None:
        return None
    if not result.get("ok"):
        raise RuntimeError(
            "daemon exec failed on %s: %s" % (alias, result.get("error", "unknown"))
        )
    return result.get("stdout", ""), result.get("stderr", ""), result.get("rc", -1)


def load_server_config() -> dict:
    """Read scripts/server_config.json (next to this file); {} if missing."""
    import json

    path = Path(__file__).with_name("server_config.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_server(config: dict, server_name: str | None = None) -> dict:
    """Resolve a server entry from the config.

    Multi-server configs look up ``servers[name]`` merged over ``defaults``;
    flat single-server configs pass through their top-level fields. Unknown
    names raise SystemExit with the list of available servers.
    """
    if server_name and isinstance(config.get("servers"), dict):
        servers = config["servers"]
        if server_name in servers:
            return {**config.get("defaults", {}), **servers[server_name]}
        available = ", ".join(servers.keys())
        raise SystemExit(
            f"Error: Server '{server_name}' not found. Available: {available}"
        )
    # Fallback to flat config for backward compatibility
    return {
        "host": config.get("host", ""),
        "port": config.get("port", 22),
        "username": config.get("username", "root"),
        "password": config.get("password", ""),
        "key_file": config.get("key_file", ""),
        "host_key_policy": config.get("host_key_policy", ""),
    }


def msys_unconvert(path):
    """Undo Git Bash (MSYS) argument rewriting for REMOTE path arguments.

    Git Bash rewrites absolute POSIX-looking argv entries before exec:
      /home/x  ->  <msys-root>/home/x   (e.g. E:/Git/home/x)
      /tmp/x   ->  <windows-temp>/x
    Only apply this to arguments that are remote (SSH) paths — never to
    local paths, where the conversion is intentional.
    """
    if not isinstance(path, str) or not os.environ.get("MSYSTEM"):
        return path
    norm = path.replace("\\", "/")
    # MSYS root = parent of EXEPATH (E:\Git\bin -> E:\Git); confirmed by usr/bin
    expath = os.environ.get("EXEPATH", "").replace("\\", "/")
    root = str(Path(expath).parent).replace("\\", "/") if expath else ""
    if root and os.path.isdir(os.path.join(root, "usr", "bin")):
        prefix = root + "/"
        if norm.lower().startswith(prefix.lower()):
            return norm[len(root):]
    # /tmp style conversion onto the Windows temp dir
    tmp = os.environ.get("TMP") or os.environ.get("TEMP")
    if tmp:
        tnorm = tmp.replace("\\", "/").rstrip("/")
        if norm.startswith(tnorm + "/"):
            return "/tmp" + norm[len(tnorm):]
    return path


def quote_remote_path(path) -> str:
    return shlex.quote(str(path))
