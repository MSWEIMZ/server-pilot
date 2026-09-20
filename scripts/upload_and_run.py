#!/usr/bin/env python3
"""Upload a local file to server and optionally run it.

Usage:
    python upload_and_run.py ./local_script.py /remote/path/script.py
    python upload_and_run.py ./local_script.py /remote/path/script.py --run
    python upload_and_run.py ./local_script.py /remote/path/script.py --run --args "--epochs 100"
"""
import argparse, json, os, sys

from security import (
    connect_ssh,
    load_server_config as load_config,
    msys_unconvert,
    pooled_exec,
    quote_remote_path,
    resolve_server,
)

def main():
    pa = argparse.ArgumentParser(description="Upload file to server and optionally run it")
    pa.add_argument("local", help="Local file path")
    pa.add_argument("remote", help="Remote file path")
    pa.add_argument("--run", action="store_true", help="Run the uploaded file")
    pa.add_argument("--args", default="", help="Arguments to pass when running")
    pa.add_argument("--python", default="/root/miniconda3/bin/python", help="Python interpreter on server")
    pa.add_argument("--server", "-s", help="Server name")
    a = pa.parse_args()
    a.remote = msys_unconvert(a.remote)  # undo Git Bash path rewriting

    if not os.path.exists(a.local):
        print(f"Error: {a.local} not found", file=sys.stderr); return 1

    cfg = load_config()
    srv = resolve_server(cfg, a.server)
    if not srv.get("host"):
        print("Error: No host.", file=sys.stderr); return 1

    ssh = connect_ssh(srv["host"], srv.get("port", 22), srv.get("username", "root"),
                      srv.get("password", ""), srv.get("key_file", ""), host_key_policy=srv.get("host_key_policy", ""))

    try:
        # Ensure remote directory exists
        remote_dir = os.path.dirname(a.remote)
        if remote_dir:
            ssh.exec_command("mkdir -p " + quote_remote_path(remote_dir))

        # Upload via SFTP
        sftp = ssh.open_sftp()
        sftp.put(a.local, a.remote)
        sftp.close()
        print(f"Uploaded: {a.local} -> {a.remote}")

        if a.run:
            cmd = f"{quote_remote_path(a.python)} {quote_remote_path(a.remote)} {a.args}".strip()
            print(f"Running: {cmd}")
            # Prefer the pool daemon for command execution (SFTP upload above
            # always uses the direct connection). None means the daemon is
            # unavailable: fall through to the original direct streaming path.
            alias = a.server if (a.server and "servers" in cfg) else "default"
            pooled = pooled_exec(alias, cmd)
            if pooled is not None:
                out, err, exit_code = pooled
                if out: print(out, end="")
                if err: print(err, end="", file=sys.stderr)
                if exit_code:
                    print(f"\nExit code: {exit_code}", file=sys.stderr)
                return exit_code
            _, stdout, stderr = ssh.exec_command(cmd, timeout=0)
            # Stream output in real-time
            import select
            channel = stdout.channel
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    print(channel.recv(4096).decode("utf-8", errors="replace"), end="")
                elif channel.recv_stderr_ready():
                    print(channel.recv_stderr(4096).decode("utf-8", errors="replace"), end="", file=sys.stderr)
                else:
                    import time; time.sleep(0.1)
            # Read remaining
            while channel.recv_ready():
                print(channel.recv(4096).decode("utf-8", errors="replace"), end="")
            while channel.recv_stderr_ready():
                print(channel.recv_stderr(4096).decode("utf-8", errors="replace"), end="", file=sys.stderr)
            exit_code = channel.recv_exit_status()
            if exit_code:
                print(f"\nExit code: {exit_code}", file=sys.stderr)
            return exit_code
    finally:
        ssh.close()

if __name__ == "__main__":
    sys.exit(main() or 0)
