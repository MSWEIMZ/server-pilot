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
import subprocess
import sys

def ensure_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko
        return paramiko

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

def resolve_server(config, server_name=None):
    """Resolve server config. Supports multi-server configs."""
    if server_name and "servers" in config:
        servers = config["servers"]
        if server_name in servers:
            return {**config.get("defaults", {}), **servers[server_name]}
        else:
            available = ", ".join(servers.keys())
            print(f"Error: Server '{server_name}' not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
    # Fallback to flat config for backward compatibility
    return {
        "host": config.get("host", ""),
        "port": config.get("port", 22),
        "username": config.get("username", "root"),
        "password": config.get("password", ""),
        "key_file": config.get("key_file", ""),
    }

def connect_ssh(host, port, username, password=None, key_file=None, timeout=15, retries=3):
    """Create SSH connection with key or password auth, with keepalive and retry."""
    import time as _time
    paramiko = ensure_paramiko()
    for attempt in range(retries):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kwargs = {"hostname": host, "port": int(port), "username": username, "timeout": timeout}
            if key_file and os.path.exists(os.path.expanduser(key_file)):
                connect_kwargs["key_filename"] = os.path.expanduser(key_file)
            elif password:
                connect_kwargs["password"] = password
            else:
                default_keys = [
                    os.path.expanduser("~/.ssh/id_rsa"),
                    os.path.expanduser("~/.ssh/id_ed25519"),
                    os.path.expanduser("~/.ssh/id_ecdsa"),
                ]
                found_key = next((k for k in default_keys if os.path.exists(k)), None)
                if found_key:
                    connect_kwargs["key_filename"] = found_key
                else:
                    print("Error: No SSH key found and no password provided.", file=sys.stderr)
                    print("Set key_file in server_config.json or use --key / --pass.", file=sys.stderr)
                    sys.exit(1)
            ssh.connect(**connect_kwargs)
            transport = ssh.get_transport()
            if transport:
                transport.set_keepalive(15)
            return ssh
        except Exception as e:
            if attempt < retries - 1:
                _time.sleep(2 * (attempt + 1))
            else:
                raise

def run_command(host, port, username, password, key_file, command, timeout=30):
    ssh = connect_ssh(host, port, username, password, key_file)
    try:
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"stdout": out, "stderr": err, "exit_code": exit_code}
    finally:
        ssh.close()

def upload_file(host, port, username, password, key_file, local_path, remote_path):
    ssh = connect_ssh(host, port, username, password, key_file)
    try:
        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        return {"success": True, "message": f"Uploaded {local_path} -> {remote_path}"}
    finally:
        ssh.close()

def download_file(host, port, username, password, key_file, remote_path, local_path):
    ssh = connect_ssh(host, port, username, password, key_file)
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

def main():
    parser = argparse.ArgumentParser(description="SSH remote command executor")
    parser.add_argument("command", nargs="?", help="Command to execute")
    parser.add_argument("--server", "-s", help="Server name (multi-server config)")
    parser.add_argument("--host", help="SSH host")
    parser.add_argument("--port", type=int, help="SSH port")
    parser.add_argument("--user", help="SSH username")
    parser.add_argument("--pass", dest="password", help="SSH password")
    parser.add_argument("--key", dest="key_file", help="Path to SSH private key")
    parser.add_argument("--timeout", type=int, default=30, help="Command timeout")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--upload", nargs=2, metavar=("LOCAL", "REMOTE"), help="Upload file")
    parser.add_argument("--download", nargs=2, metavar=("REMOTE", "LOCAL"), help="Download file")
    parser.add_argument("--list-servers", action="store_true", help="List servers")
    args = parser.parse_args()
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
    if not host:
        print("Error: No host. Use --host, --server, or configure server_config.json", file=sys.stderr)
        sys.exit(1)
    try:
        if args.upload:
            result = upload_file(host, port, username, password, key_file, args.upload[0], args.upload[1])
            print(json.dumps(result, ensure_ascii=False) if args.json else result["message"])
        elif args.download:
            result = download_file(host, port, username, password, key_file, args.download[0], args.download[1])
            print(json.dumps(result, ensure_ascii=False) if args.json else result["message"])
        elif args.command:
            result = run_command(host, port, username, password, key_file, args.command, args.timeout)
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
        err_msg = str(e)
        if "Authentication" in err_msg:
            print(f"Auth failed for {username}@{host}:{port}. Check password or SSH key.", file=sys.stderr)
        elif "timed out" in err_msg.lower() or "connect" in err_msg.lower():
            print(f"Cannot connect to {host}:{port}. Host may be down.", file=sys.stderr)
        else:
            print(f"SSH Error: {err_msg}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": err_msg, "host": host, "port": port}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
