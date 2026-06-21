#!/usr/bin/env python3
"""Remote file operations via SSH/SFTP.

Usage:
    python file_ops.py cat /remote/file                    # View file content
    python file_ops.py cat /remote/file -n 50              # Last 50 lines
    python file_ops.py ls /remote/dir                      # List directory
    python file_ops.py ls /remote/dir -t                   # Tree view
    python file_ops.py edit /remote/file                   # Download, open in editor, upload with backup
    python file_ops.py edit /remote/file --editor notepad  # Use specific editor
    python file_ops.py search /remote/dir "*.py" "import"  # Find + grep
    python file_ops.py search /remote/dir --name "*.log"   # Find by name only
    python file_ops.py sync-up ./local/dir /remote/dir     # Upload directory
    python file_ops.py sync-down /remote/dir ./local/dir   # Download directory
    python file_ops.py diff /remote/file ./local/file      # Compare remote vs local
    python file_ops.py --server gpu-box cat /remote/file   # Multi-server
"""

import argparse
import datetime
import difflib
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile


# Fix Windows console encoding
try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

def ensure_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko
        return paramiko

def load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")
    return json.load(open(p)) if os.path.exists(p) else {}

def resolve_server(cfg, name=None):
    if name and "servers" in cfg:
        s = cfg["servers"]
        if name in s: return {**cfg.get("defaults", {}), **s[name]}
        print(f"Error: Server '{name}' not found. Available: {', '.join(s.keys())}", file=sys.stderr)
        sys.exit(1)
    return {"host": cfg.get("host", ""), "port": cfg.get("port", 22),
            "username": cfg.get("username", "root"), "password": cfg.get("password", ""),
            "key_file": cfg.get("key_file", "")}

def _connect(host, port, user, pwd=None, key=None):
    paramiko = ensure_paramiko()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = {"hostname": host, "port": int(port), "username": user, "timeout": 15}
    if key and os.path.exists(os.path.expanduser(key)):
        kw["key_filename"] = os.path.expanduser(key)
    elif pwd:
        kw["password"] = pwd
    else:
        for k in ["~/.ssh/id_rsa", "~/.ssh/id_ed25519", "~/.ssh/id_ecdsa"]:
            if os.path.exists(os.path.expanduser(k)):
                kw["key_filename"] = os.path.expanduser(k)
                break
        else:
            print("Error: No SSH key or password.", file=sys.stderr)
            sys.exit(1)
    ssh.connect(**kw)
    return ssh

def _cmd(ssh, cmd, t=15):
    try:
        _, o, _ = ssh.exec_command(cmd, timeout=t)
        return o.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Error: {e}"

# ===== CAT =====
def cat_file(ssh, remote_path, lines=0, tail=False):
    """View remote file content."""
    if lines > 0:
        cmd = f"tail -{lines} '{remote_path}'" if tail else f"head -{lines} '{remote_path}'"
    else:
        cmd = f"cat '{remote_path}'"
    content = _cmd(ssh, cmd, t=30)
    if not content.strip():
        # Check if file exists
        exists = _cmd(ssh, f"test -f '{remote_path}' && echo EXISTS || echo NOT_FOUND").strip()
        if "NOT_FOUND" in exists:
            print(f"Error: File not found: {remote_path}", file=sys.stderr)
            return 1
    print(content, end="")
    return 0

# ===== LS =====
def ls_dir(ssh, remote_path, tree=False, all_files=False):
    """List remote directory."""
    if tree:
        cmd = f"find '{remote_path}' -maxdepth 3 {'-name \".*\" -o ' if all_files else ''}-print 2>/dev/null | head -200"
        # Simpler: use tree if available, fallback to find
        cmd = f"(tree -L 3 {'-a ' if all_files else ''}'{remote_path}' 2>/dev/null || find '{remote_path}' -maxdepth 3 {'-name \".*\" -o ' if all_files else ''}-print 2>/dev/null | head -200)"
        # Simplified
        cmd = f"which tree >/dev/null 2>&1 && tree -L 3 '{remote_path}' 2>/dev/null || find '{remote_path}' -maxdepth 3 -print 2>/dev/null | head -200"
    else:
        flags = "-la" if all_files else "-l"
        cmd = f"ls {flags} '{remote_path}' 2>/dev/null"
    output = _cmd(ssh, cmd, t=15)
    if "No such file" in output or "cannot access" in output:
        print(f"Error: Directory not found: {remote_path}", file=sys.stderr)
        return 1
    print(output, end="")
    return 0

# ===== EDIT =====
def edit_file(ssh, sftp, remote_path, editor=None):
    """Download remote file, open in local editor, upload with backup."""
    # Check if remote file exists
    try:
        stat = sftp.stat(remote_path)
    except FileNotFoundError:
        print(f"Error: File not found: {remote_path}", file=sys.stderr)
        return 1

    # Create local temp file
    local_dir = tempfile.mkdtemp(prefix="sp_edit_")
    local_path = os.path.join(local_dir, os.path.basename(remote_path))

    # Download
    print(f"Downloading {remote_path}...")
    sftp.get(remote_path, local_path)
    print(f"  Saved to {local_path}")

    # Get original content hash
    with open(local_path, "rb") as f:
        original = f.read()

    # Open editor
    if not editor:
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
        if not editor:
            # Auto-detect
            for e in ["code", "notepad++", "notepad", "vim", "nano"]:
                if shutil.which(e):
                    editor = e
                    break
            if not editor:
                editor = "notepad"

    print(f"Opening in {editor}...")
    try:
        subprocess.run([editor, local_path])
    except FileNotFoundError:
        print(f"Error: Editor '{editor}' not found. Install it or set EDITOR env var.", file=sys.stderr)
        return 1

    # Check if file changed
    with open(local_path, "rb") as f:
        modified = f.read()

    if modified == original:
        print("No changes made.")
        shutil.rmtree(local_dir)
        return 0

    # Create backup on remote
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{remote_path}.bak.{ts}"
    print(f"Creating backup: {backup_path}")
    _cmd(ssh, f"cp '{remote_path}' '{backup_path}'")

    # Upload modified file
    print(f"Uploading changes to {remote_path}...")
    sftp.put(local_path, remote_path)
    print("Done!")

    # Cleanup
    shutil.rmtree(local_dir)
    return 0

# ===== SEARCH =====
def search_files(ssh, remote_dir, pattern="*", grep_text=None, file_type=None, max_depth=5):
    """Find files and optionally grep content."""
    cmd_parts = [f"find '{remote_dir}' -maxdepth {max_depth}"]

    if pattern and pattern != "*":
        cmd_parts.append(f"-name '{pattern}'")

    if file_type:
        cmd_parts.append(f"-type {file_type}")

    cmd_parts.append("-print 2>/dev/null")

    find_cmd = " ".join(cmd_parts)

    if grep_text:
        # Pipe find results to grep
        cmd = f"{find_cmd} | head -500 | xargs grep -l '{grep_text}' 2>/dev/null | head -50"
        output = _cmd(ssh, cmd, t=30)
        print(f"Files matching '{pattern}' containing '{grep_text}':")
        print(output, end="")

        # Also show matching lines
        cmd2 = f"{find_cmd} | head -500 | xargs grep -n '{grep_text}' 2>/dev/null | head -100"
        output2 = _cmd(ssh, cmd2, t=30)
        if output2.strip():
            print(f"\nMatching lines:")
            print(output2, end="")
    else:
        output = _cmd(ssh, find_cmd, t=15)
        print(output, end="")

    return 0

# ===== SYNC UP =====
def sync_up(ssh, sftp, local_dir, remote_dir):
    """Upload a local directory to remote server."""
    if not os.path.isdir(local_dir):
        print(f"Error: Local directory not found: {local_dir}", file=sys.stderr)
        return 1

    # Create remote dir
    _cmd(ssh, f"mkdir -p '{remote_dir}'")

    uploaded = 0
    skipped = 0
    errors = 0

    for root, dirs, files in os.walk(local_dir):
        # Compute relative path
        rel = os.path.relpath(root, local_dir)
        remote_path = os.path.join(remote_dir, rel).replace("\\", "/")

        # Create remote subdirectory
        _cmd(ssh, f"mkdir -p '{remote_path}'")

        for f in files:
            local_file = os.path.join(root, f)
            remote_file = f"{remote_path}/{f}"

            # Check if remote file exists and has same size
            try:
                rstat = sftp.stat(remote_file)
                lstat = os.stat(local_file)
                if rstat.st_size == lstat.st_size:
                    skipped += 1
                    continue
            except (FileNotFoundError, IOError):
                pass

            try:
                sftp.put(local_file, remote_file)
                uploaded += 1
                print(f"  UP  {rel}/{f}")
            except Exception as e:
                errors += 1
                print(f"  ERR {rel}/{f}: {e}")

    print(f"\nSync complete: {uploaded} uploaded, {skipped} skipped, {errors} errors")
    return 0

# ===== SYNC DOWN =====
def sync_down(ssh, sftp, remote_dir, local_dir):
    """Download a remote directory to local machine."""
    # Check if remote dir exists
    exists = _cmd(ssh, f"test -d '{remote_dir}' && echo OK").strip()
    if "OK" not in exists:
        print(f"Error: Remote directory not found: {remote_dir}", file=sys.stderr)
        return 1

    os.makedirs(local_dir, exist_ok=True)

    # Get file list
    file_list = _cmd(ssh, f"find '{remote_dir}' -type f 2>/dev/null | head -1000", t=30)
    files = [f.strip() for f in file_list.strip().split("\n") if f.strip()]

    downloaded = 0
    skipped = 0
    errors = 0

    for remote_file in files:
        # Compute local path
        rel = remote_file[len(remote_dir):].lstrip("/")
        local_file = os.path.join(local_dir, rel)
        local_subdir = os.path.dirname(local_file)
        os.makedirs(local_subdir, exist_ok=True)

        # Check if local file exists and has same size
        try:
            rstat = sftp.stat(remote_file)
            if os.path.exists(local_file):
                lstat = os.stat(local_file)
                if rstat.st_size == lstat.st_size:
                    skipped += 1
                    continue
        except (FileNotFoundError, IOError):
            pass

        try:
            sftp.get(remote_file, local_file)
            downloaded += 1
            print(f"  DL  {rel}")
        except Exception as e:
            errors += 1
            print(f"  ERR {rel}: {e}")

    print(f"\nSync complete: {downloaded} downloaded, {skipped} skipped, {errors} errors")
    return 0

# ===== DIFF =====
def diff_file(ssh, sftp, remote_path, local_path):
    """Compare remote file with local file."""
    if not os.path.exists(local_path):
        print(f"Error: Local file not found: {local_path}", file=sys.stderr)
        return 1

    # Download remote file to temp
    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=os.path.basename(remote_path), delete=False)
    try:
        sftp.get(remote_path, tmp.name)
    except FileNotFoundError:
        print(f"Error: Remote file not found: {remote_path}", file=sys.stderr)
        tmp.close()
        os.unlink(tmp.name)
        return 1
    tmp.close()

    # Compare
    with open(tmp.name, "r", encoding="utf-8", errors="replace") as f:
        remote_lines = f.readlines()
    with open(local_path, "r", encoding="utf-8", errors="replace") as f:
        local_lines = f.readlines()

    diff = list(difflib.unified_diff(
        local_lines, remote_lines,
        fromfile=f"local:{local_path}",
        tofile=f"remote:{remote_path}",
        lineterm=""
    ))

    if diff:
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                print(f"\033[32m{line}\033[0m")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"\033[31m{line}\033[0m")
            else:
                print(line)
        return 1
    else:
        print("Files are identical.")
        return 0

    os.unlink(tmp.name)

def main():
    pa = argparse.ArgumentParser(description="Remote file operations")
    sub = pa.add_subparsers(dest="command")

    # cat
    p_cat = sub.add_parser("cat", help="View file content")
    p_cat.add_argument("path", help="Remote file path")
    p_cat.add_argument("-n", "--lines", type=int, default=0, help="Number of lines (0=all)")
    p_cat.add_argument("-t", "--tail", action="store_true", help="Show last N lines")

    # ls
    p_ls = sub.add_parser("ls", help="List directory")
    p_ls.add_argument("path", nargs="?", default=".", help="Remote directory path")
    p_ls.add_argument("-t", "--tree", action="store_true", help="Tree view")
    p_ls.add_argument("-a", "--all", action="store_true", help="Show hidden files")

    # edit
    p_edit = sub.add_parser("edit", help="Edit remote file locally")
    p_edit.add_argument("path", help="Remote file path")
    p_edit.add_argument("--editor", help="Editor to use")

    # search
    p_search = sub.add_parser("search", help="Find and grep files")
    p_search.add_argument("dir", help="Remote directory to search")
    p_search.add_argument("--name", "-n", default="*", help="Filename pattern")
    p_search.add_argument("--grep", "-g", help="Text to search for")
    p_search.add_argument("--type", "-t", choices=["f", "d"], help="File type (f=file, d=dir)")
    p_search.add_argument("--depth", "-d", type=int, default=5, help="Max search depth")

    # sync-up
    p_up = sub.add_parser("sync-up", help="Upload directory")
    p_up.add_argument("local", help="Local directory")
    p_up.add_argument("remote", help="Remote directory")

    # sync-down
    p_down = sub.add_parser("sync-down", help="Download directory")
    p_down.add_argument("remote", help="Remote directory")
    p_down.add_argument("local", help="Local directory")

    # diff
    p_diff = sub.add_parser("diff", help="Compare remote vs local file")
    p_diff.add_argument("remote", help="Remote file path")
    p_diff.add_argument("local", help="Local file path")

    # Global options
    pa.add_argument("--server", "-s", help="Server name")
    pa.add_argument("--json", action="store_true", help="JSON output")

    args = pa.parse_args()
    if not args.command:
        pa.print_help()
        return 1

    cfg = load_config()
    srv = resolve_server(cfg, args.server)
    if not srv.get("host"):
        print("Error: No host configured.", file=sys.stderr)
        return 1

    ssh = _connect(srv["host"], srv.get("port", 22), srv.get("username", "root"),
                   srv.get("password", ""), srv.get("key_file", ""))

    try:
        paramiko = ensure_paramiko()
        sftp = ssh.open_sftp()

        if args.command == "cat":
            return cat_file(ssh, args.path, args.lines, args.tail)
        elif args.command == "ls":
            return ls_dir(ssh, args.path, args.tree, args.all)
        elif args.command == "edit":
            return edit_file(ssh, sftp, args.path, args.editor)
        elif args.command == "search":
            return search_files(ssh, args.dir, args.name, args.grep, args.type, args.depth)
        elif args.command == "sync-up":
            return sync_up(ssh, sftp, args.local, args.remote)
        elif args.command == "sync-down":
            return sync_down(ssh, sftp, args.remote, args.local)
        elif args.command == "diff":
            return diff_file(ssh, sftp, args.remote, args.local)

        sftp.close()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        ssh.close()

if __name__ == "__main__":
    sys.exit(main() or 0)
