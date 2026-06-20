#!/usr/bin/env python3
"""SSH remote command executor via paramiko.

Usage:
    python ssh_exec.py "command1 && command2"
    python ssh_exec.py --host HOST --port PORT --user USER --pass PASS "command"
    python ssh_exec.py --json "command"  (output as JSON)

Default server config is read from server_config.json in the same directory.
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

def run_command(host, port, username, password, command, timeout=30):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=int(port), username=username, password=password, timeout=15)
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"stdout": out, "stderr": err, "exit_code": exit_code}
    finally:
        ssh.close()

def upload_file(host, port, username, password, local_path, remote_path):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=int(port), username=username, password=password, timeout=15)
        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        return True
    finally:
        ssh.close()

def download_file(host, port, username, password, remote_path, local_path):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, port=int(port), username=username, password=password, timeout=15)
        sftp = ssh.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()
        return True
    finally:
        ssh.close()

def main():
    parser = argparse.ArgumentParser(description="SSH remote command executor")
    parser.add_argument("command", nargs="?", help="Command to execute on remote server")
    parser.add_argument("--host", help="SSH host")
    parser.add_argument("--port", type=int, help="SSH port")
    parser.add_argument("--user", help="SSH username")
    parser.add_argument("--pass", dest="password", help="SSH password")
    parser.add_argument("--timeout", type=int, default=30, help="Command timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--upload", nargs=2, metavar=("LOCAL", "REMOTE"), help="Upload file")
    parser.add_argument("--download", nargs=2, metavar=("REMOTE", "LOCAL"), help="Download file")

    args = parser.parse_args()
    config = load_config()

    host = args.host or config.get("host", "")
    port = args.port or config.get("port", 22)
    username = args.user or config.get("username", "root")
    password = args.password or config.get("password", "")

    if not host:
        print("Error: No host. Use --host or configure server_config.json", file=sys.stderr)
        sys.exit(1)

    if args.upload:
        ok = upload_file(host, port, username, password, args.upload[0], args.upload[1])
        print("Upload: " + ("OK" if ok else "FAILED"))
    elif args.download:
        ok = download_file(host, port, username, password, args.download[0], args.download[1])
        print("Download: " + ("OK" if ok else "FAILED"))
    elif args.command:
        result = run_command(host, port, username, password, args.command, args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            if result["stdout"]:
                print(result["stdout"], end="")
            if result["stderr"]:
                print(result["stderr"], end="", file=sys.stderr)
        sys.exit(result["exit_code"])
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
