#!/usr/bin/env python3
"""Upload a local file to server and optionally run it.

Usage:
    python upload_and_run.py ./local_script.py /remote/path/script.py
    python upload_and_run.py ./local_script.py /remote/path/script.py --run
    python upload_and_run.py ./local_script.py /remote/path/script.py --run --args "--epochs 100"
"""
import argparse, json, os, sys, subprocess

def ensure_paramiko():
    try:
        import paramiko; return paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko; return paramiko

def load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    return json.load(open(p)) if os.path.exists(p) else {}

def resolve_server(cfg, name=None):
    if name and "servers" in cfg:
        s = cfg["servers"]
        if name in s: return {**cfg.get("defaults", {}), **s[name]}
        print(f"Error: Server '{name}' not found.", file=sys.stderr); sys.exit(1)
    return {"host": cfg.get("host", ""), "port": cfg.get("port", 22),
            "username": cfg.get("username", "root"), "password": cfg.get("password", ""),
            "key_file": cfg.get("key_file", "")}

def connect(host, port, user, pwd=None, key=None):
    import paramiko, time
    for attempt in range(3):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw = {"hostname": host, "port": int(port), "username": user, "timeout": 15}
            if key and os.path.exists(os.path.expanduser(key)):
                kw["key_filename"] = os.path.expanduser(key)
            elif pwd:
                kw["password"] = pwd
            else:
                for k in ["~/.ssh/id_rsa", "~/.ssh/id_ed25519"]:
                    if os.path.exists(os.path.expanduser(k)):
                        kw["key_filename"] = os.path.expanduser(k); break
            ssh.connect(**kw)
            ssh.get_transport().set_keepalive(15)
            return ssh
        except Exception as e:
            if attempt < 2: time.sleep(2 * (attempt + 1))
            else: raise

def main():
    pa = argparse.ArgumentParser(description="Upload file to server and optionally run it")
    pa.add_argument("local", help="Local file path")
    pa.add_argument("remote", help="Remote file path")
    pa.add_argument("--run", action="store_true", help="Run the uploaded file")
    pa.add_argument("--args", default="", help="Arguments to pass when running")
    pa.add_argument("--python", default="/root/miniconda3/bin/python", help="Python interpreter on server")
    pa.add_argument("--server", "-s", help="Server name")
    a = pa.parse_args()

    if not os.path.exists(a.local):
        print(f"Error: {a.local} not found", file=sys.stderr); return 1

    cfg = load_config()
    srv = resolve_server(cfg, a.server)
    if not srv.get("host"):
        print("Error: No host.", file=sys.stderr); return 1

    ssh = connect(srv["host"], srv.get("port", 22), srv.get("username", "root"),
                  srv.get("password", ""), srv.get("key_file", ""))

    try:
        # Ensure remote directory exists
        remote_dir = os.path.dirname(a.remote)
        if remote_dir:
            ssh.exec_command(f"mkdir -p {remote_dir}")

        # Upload via SFTP
        sftp = ssh.open_sftp()
        sftp.put(a.local, a.remote)
        sftp.close()
        print(f"Uploaded: {a.local} -> {a.remote}")

        if a.run:
            cmd = f"{a.python} {a.remote} {a.args}".strip()
            print(f"Running: {cmd}")
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