# Server Pilot Consolidation and Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate Server Pilot into one source/configuration directory and make local dashboard and SSH access safe by default.

**Architecture:** Add a small shared Python security module for dependency checks, known-hosts validation, numeric request validation, and POSIX shell quoting. Existing command scripts call that module instead of duplicating permissive Paramiko setup. The dashboard is local-only by default; an explicit remote mode is token-protected. Windows Skill installations become junctions to the canonical source after timestamped backup.

**Tech Stack:** Python 3, Paramiko, unittest, PowerShell, Git.

---

### Task 1: Establish the canonical source and test harness

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_security.py`
- Modify: `.gitignore`
- Modify: `scripts/server_config.example.json`

- [ ] **Step 1: Write failing tests for safe primitive behavior**

```python
from scripts.security import clamp_log_lines, validate_pid, quote_remote_path

def test_pid_and_log_line_limits():
    assert validate_pid("123") == 123
    assert clamp_log_lines("200") == 200
    assert clamp_log_lines("999999") == 1000
```

- [ ] **Step 2: Run the security tests before implementation**

Run: `python -m unittest tests.test_security -v`
Expected: FAIL because `scripts.security` does not exist.

- [ ] **Step 3: Add ignored local security state**

Add `scripts/known_hosts` and `backups/` to `.gitignore`; document optional `known_hosts_file` in the configuration example without adding host data.

- [ ] **Step 4: Commit the test scaffold**

```bash
git add tests .gitignore scripts/server_config.example.json
git commit -m "test: add security primitive coverage"
```

### Task 2: Centralize SSH security and dependency behavior

**Files:**
- Create: `scripts/security.py`
- Modify: `scripts/server_monitor.py`
- Modify: `scripts/ssh_exec.py`
- Modify: `scripts/file_ops.py`
- Modify: `scripts/task_mgr.py`
- Modify: `scripts/upload_and_run.py`
- Test: `tests/test_security.py`

- [ ] **Step 1: Implement shared helpers**

```python
def require_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required; install it in the active environment") from exc
    return paramiko

def connect_ssh(host, port, username, password=None, key_file=None, timeout=15, retries=3):
    client = require_paramiko().SSHClient()
    client.load_system_host_keys()
    client.load_host_keys(project_known_hosts_path())
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    # Connect with password or explicit key; retry only transport failures.
```

`validate_pid` accepts only ASCII decimal positive integers, `clamp_log_lines` converts valid input to `1..1000`, and `quote_remote_path` uses `shlex.quote`.

- [ ] **Step 2: Replace duplicated AutoAddPolicy and implicit pip code**

Each command script imports the shared connection/dependency functions. No script calls pip or `AutoAddPolicy`; a rejected host key error tells the user to verify and add the key to `scripts/known_hosts`.

- [ ] **Step 3: Run focused tests and syntax compilation**

Run: `python -m unittest tests.test_security -v && python -m compileall -q scripts`
Expected: all tests pass and the compiler emits no errors.

- [ ] **Step 4: Commit the SSH hardening**

```bash
git add scripts tests
git commit -m "fix: require verified SSH host keys"
```

### Task 3: Secure the dashboard request surface

**Files:**
- Modify: `scripts/web/dashboard.py`
- Modify: `scripts/web/dashboard.html`
- Create: `tests/test_dashboard_security.py`

- [ ] **Step 1: Write failing tests for listener and request validation**

```python
from scripts.web.dashboard import build_server, parse_log_request

def test_dashboard_defaults_to_loopback():
    assert build_server(port=8765).server_address[0] == "127.0.0.1"

def test_log_request_rejects_non_numeric_pid():
    assert parse_log_request({"pid": ["1;id"]}) is None
```

- [ ] **Step 2: Implement local-only and authenticated remote mode**

`dashboard.py` defaults to `127.0.0.1`, emits no `Access-Control-Allow-Origin` header, and adds `--bind`, `--allow-remote`, and `--token` arguments. Any non-loopback bind requires both `--allow-remote` and a token. API handlers require `Authorization: Bearer <token>` only in remote mode.

- [ ] **Step 3: Bound and sanitize remote log operations**

The handler validates PID and lines before calling `get_process_log`; all log filenames, working directories, and fd targets are passed through `quote_remote_path`. Remove environment values from API detail responses. Escape every server-provided string interpolated into dashboard HTML.

- [ ] **Step 4: Run dashboard tests and command-line smoke check**

Run: `python -m unittest tests.test_dashboard_security -v && python scripts/web/dashboard.py --help`
Expected: all tests pass and help lists the new bind/authentication options.

- [ ] **Step 5: Commit the dashboard hardening**

```bash
git add scripts/web tests
git commit -m "fix: restrict dashboard exposure"
```

### Task 4: Document the safe operation model

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `README_CN.md`

- [ ] **Step 1: Update setup and trust instructions**

Document the canonical repository path, the one-time known-hosts verification requirement, explicit dependency installation, local-only dashboard default, and token-protected remote dashboard command.

- [ ] **Step 2: Remove insecure fallback guidance**

Delete instructions that ask users to bypass internal scripts with ad-hoc raw Paramiko code or to accept host keys automatically.

- [ ] **Step 3: Commit documentation**

```bash
git add SKILL.md README.md README_CN.md
git commit -m "docs: describe secure local deployment"
```

### Task 5: Migrate local Skill installations without data loss

**Files:**
- Create: `scripts/migrate_local_installations.ps1`
- Modify: `README_CN.md`

- [ ] **Step 1: Implement a dry-run default migration script**

The script defines the canonical source and six actual installation paths. It verifies every resolved path is under `C:\Users\WEI`, copies the old directory to `backups\<timestamp>\<safe-name>`, renames the old directory, and creates a Junction to the canonical source. It supports `-Apply`; without it, it lists operations only.

- [ ] **Step 2: Migrate the selected three-server configuration**

Before creating links, copy the complete Codex configuration to the canonical `scripts/server_config.json`; copy every legacy configuration to the timestamped backup. Do not print configuration content.

- [ ] **Step 3: Run dry-run and apply migration**

Run: `powershell -ExecutionPolicy Bypass -File scripts/migrate_local_installations.ps1`
Expected: six backups and junction operations are listed with no mutations.

Run: `powershell -ExecutionPolicy Bypass -File scripts/migrate_local_installations.ps1 -Apply`
Expected: six installations resolve to the canonical source; the cache paths and `Documents` workspace are unchanged.

- [ ] **Step 4: Verify configuration and links without exposing secrets**

Run a JSON structure check reporting only server count and a path-resolution check for every junction.

- [ ] **Step 5: Commit migration tooling and documentation**

```bash
git add scripts/migrate_local_installations.ps1 README_CN.md
git commit -m "feat: add safe local skill migration"
```

### Task 6: Final verification

**Files:**
- Test: `tests/test_security.py`
- Test: `tests/test_dashboard_security.py`

- [ ] **Step 1: Run the complete test and compilation suite**

Run: `python -m unittest discover -s tests -v && python -m compileall -q scripts`
Expected: all tests pass and compilation exits 0.

- [ ] **Step 2: Inspect repository and migration state**

Run: `git status --short`, `git log --oneline -6`, and a read-only junction/config metadata check. Confirm no tracked credential file and no uncommitted implementation change remains.

- [ ] **Step 3: Report completed changes and remaining manual trust action**

State that SSH connections require host fingerprints in known_hosts and provide no remote connection claim.
