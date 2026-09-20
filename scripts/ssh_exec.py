#!/usr/bin/env python3
"""SSH remote command executor via paramiko.

Supports password auth, SSH key auth, and multi-server configs.

Usage:
    python ssh_exec.py "command"
    python ssh_exec.py --server myserver "command"
    python ssh_exec.py --host HOST --port PORT --user USER --key ~/.ssh/id_rsa "command"
    python ssh_exec.py --json "command"
    python ssh_exec.py --upload ./local.txt /remote/path/
    python ssh_exec.py --download /remote/path/file.txt ./local.txt
    python ssh_exec.py --list-servers
"""

import argparse
import json
import os
import sys

from security import (
    DEFAULT_CONNECT_TIMEOUT,
    connect_ssh,
    describe_error,
    exec_remote,
    load_server_config as load_config,
    msys_unconvert,
    normalize_read_timeout,
    pooled_exec,
    resolve_read_timeout,
    resolve_server,
)

def run_command(host, port, username, password, key_file, command,
                read_timeout=None, host_key_policy=None, connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                alias=None):
    """Run one remote command.

    ``read_timeout`` limits how long the client waits for channel output and
    defaults to *unlimited*, so long commands are not aborted by a short
    client-side timeout.  ``connect_timeout`` only bounds TCP/SSH handshake.

    ``alias`` is the server_config.json name; when set, the command is first
    attempted through the connection-pool daemon and only falls back to the
    direct connection below when the daemon answers "unavailable" (None).
    """
    if alias is not None:
        result = pooled_exec(alias, command, read_timeout=read_timeout)
        if result is not None:
            out, err, exit_code = result
            return {"stdout": out, "stderr": err, "exit_code": exit_code}
    ssh = connect_ssh(
        host, port, username, password, key_file,
        timeout=connect_timeout, host_key_policy=host_key_policy,
    )
    try:
        out, err, exit_code = exec_remote(ssh, command, read_timeout=read_timeout)
        return {"stdout": out, "stderr": err, "exit_code": exit_code}
    finally:
        ssh.close()

def upload_file(host, port, username, password, key_file, local_path, remote_path, host_key_policy=None):
    ssh = connect_ssh(host, port, username, password, key_file, host_key_policy=host_key_policy)
    try:
        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        return {"success": True, "message": f"Uploaded {local_path} -> {remote_path}"}
    finally:
        ssh.close()

def download_file(host, port, username, password, key_file, remote_path, local_path, host_key_policy=None):
    ssh = connect_ssh(host, port, username, password, key_file, host_key_policy=host_key_policy)
    try:
        sftp = ssh.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()
        return {"success": True, "message": f"Downloaded {remote_path} -> {local_path}"}
    finally:
        ssh.close()

def list_servers(config):
    if "servers" in config:
        print("Configured servers:")
        for name, srv in config["servers"].items():
            host = srv.get("host", "?")
            user = srv.get("username", "root")
            port = srv.get("port", 22)
            auth = "key" if srv.get("key_file") else "password"
            print(f"  {name:15s}  {user}@{host}:{port}  ({auth})")
    else:
        host = config.get("host", "not configured")
        print(f"Single server mode: {host}")
        print("Tip: Use 'servers' key in server_config.json for multi-server support.")

def parse_timeout_arg(value):
    """argparse converter: seconds, or 0/none for an unlimited read timeout."""
    if isinstance(value, str) and value.strip().lower() in {"none", "off", "unlimited"}:
        return None
    try:
        return normalize_read_timeout(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main():
    parser = argparse.ArgumentParser(description="SSH remote command executor")
    parser.add_argument("command", nargs="?", help="Command to execute")
    parser.add_argument("--server", "-s", help="Server name (multi-server config)")
    parser.add_argument("--host", help="SSH host")
    parser.add_argument("--port", type=int, help="SSH port")
    parser.add_argument("--user", help="SSH username")
    parser.add_argument("--pass", dest="password", help="SSH password")
    parser.add_argument("--key", dest="key_file", help="Path to SSH private key")
    parser.add_argument(
        "--timeout", type=parse_timeout_arg, default=None, metavar="SECONDS",
        help="Client read timeout for the command channel. Default: no timeout "
             "(long commands keep running). Use 0 or 'none' to force no timeout; "
             "the SP_READ_TIMEOUT environment variable overrides this flag.",
    )
    parser.add_argument(
        "--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT,
        metavar="SECONDS", help="TCP/SSH handshake timeout (default: 15)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--upload", nargs=2, metavar=("LOCAL", "REMOTE"), help="Upload file")
    parser.add_argument("--download", nargs=2, metavar=("REMOTE", "LOCAL"), help="Download file")
    parser.add_argument("--list-servers", action="store_true", help="List servers")
    args = parser.parse_args()
    # Undo Git Bash path rewriting on remote path arguments
    if args.upload:
        args.upload[1] = msys_unconvert(args.upload[1])
    if args.download:
        args.download[0] = msys_unconvert(args.download[0])
    config = load_config()
    if args.list_servers:
        list_servers(config)
        return
    srv = resolve_server(config, args.server)
    host = args.host or srv.get("host", "")
    port = args.port or srv.get("port", 22)
    username = args.user or srv.get("username", "root")
    password = args.password or srv.get("password", "")
    key_file = args.key_file or srv.get("key_file", "")
    host_key_policy = srv.get("host_key_policy", "")
    if not host:
        print("Error: No host. Use --host, --server, or configure server_config.json", file=sys.stderr)
        sys.exit(1)
    # The pool daemon only knows server_config.json aliases; CLI connection
    # overrides (--host/--port/--user/--pass/--key) may target a different
    # machine, so they bypass the pool and keep the direct path.
    alias = None
    if not any((args.host, args.port, args.user, args.password, args.key_file)):
        alias = args.server if (args.server and "servers" in config) else "default"
    try:
        if args.upload:
            result = upload_file(host, port, username, password, key_file, args.upload[0], args.upload[1], host_key_policy)
            print(json.dumps(result, ensure_ascii=False) if args.json else result["message"])
        elif args.download:
            result = download_file(host, port, username, password, key_file, args.download[0], args.download[1], host_key_policy)
            print(json.dumps(result, ensure_ascii=False) if args.json else result["message"])
        elif args.command:
            result = run_command(
                host, port, username, password, key_file, args.command,
                args.timeout, host_key_policy, args.connect_timeout,
                alias=alias,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                if result["stdout"]: print(result["stdout"], end="")
                if result["stderr"]: print(result["stderr"], end="", file=sys.stderr)
            sys.exit(result["exit_code"])
        else:
            parser.print_help()
            sys.exit(1)
    except Exception as e:
        err_msg = describe_error(e)
        if "Authentication" in err_msg:
            print(f"Auth failed for {username}@{host}:{port}. Check password or SSH key.", file=sys.stderr)
        elif isinstance(e, TimeoutError) or "timed out" in err_msg.lower():
            print(f"SSH Error: {err_msg}", file=sys.stderr)
        elif "connect" in err_msg.lower():
            print(f"Cannot connect to {host}:{port}. Host may be down. [{err_msg}]", file=sys.stderr)
        else:
            print(f"SSH Error: {err_msg}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": err_msg, "host": host, "port": port}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
