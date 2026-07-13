"""Shared local security helpers for Server Pilot scripts."""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path


MAX_LOG_LINES = 1000


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


def configure_host_key_policy(client, paramiko) -> None:
    client.load_system_host_keys()
    known_hosts = project_known_hosts_path()
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())


def _key_filename(key_file: str | None) -> str | None:
    if key_file:
        path = os.path.expanduser(key_file)
        if os.path.exists(path):
            return path
    for candidate in ("~/.ssh/id_ed25519", "~/.ssh/id_ecdsa", "~/.ssh/id_rsa"):
        path = os.path.expanduser(candidate)
        if os.path.exists(path):
            return path
    return None


def connect_ssh(host, port, username, password=None, key_file=None, timeout=15, retries=3):
    """Connect using only known host keys; unknown keys are rejected."""
    paramiko = require_paramiko()
    for attempt in range(retries):
        client = paramiko.SSHClient()
        try:
            configure_host_key_policy(client, paramiko)
            kwargs = {"hostname": host, "port": int(port), "username": username, "timeout": timeout}
            key = _key_filename(key_file)
            if key:
                kwargs["key_filename"] = key
            elif password:
                kwargs["password"] = password
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
