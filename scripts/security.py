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


def quote_remote_path(path) -> str:
    return shlex.quote(str(path))
