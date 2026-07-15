# A40 Process Tree And Safe Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse one training process tree into one Dashboard card and read A40 logs through the `tee` file behind a pipe without ever tailing the pipe itself.

**Architecture:** Extend the current-user `ps` parser to select process-tree roots and aggregate their worker resources. Add a shared safe log-source resolver in `server_monitor.py`; regular files and validated same-user `tee` destinations may be tailed, while pipes, terminals, sockets, and unresolved sources are never opened for reading.

**Tech Stack:** Python 3 standard library, Paramiko SSH command execution, `unittest`/`pytest`, existing HTML/JavaScript Dashboard.

---

### Task 1: Collapse training workers by process tree

**Files:**
- Modify: `tests/test_dashboard_myjobs.py`
- Modify: `scripts/web/dashboard.py`
- Modify: `scripts/web/dashboard.html`

- [ ] **Step 1: Write the failing process-tree test**

Add child rows whose commands differ from the parent (`python` and multiprocessing-style commands). Assert that only the root card remains, CPU/RAM are summed, `worker_count` is exposed, and descendants of `python -m unittest` are excluded.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest -q tests/test_dashboard_myjobs.py::DashboardMyjobsTests::test_native_collector_collapses_worker_tree`

Expected: FAIL because the current parser returns child cards and does not expose aggregated worker metadata.

- [ ] **Step 3: Implement root selection and aggregation**

Build `raw_by_pid` from every current-user process. For each task candidate, walk `ppid` ancestors: discard it when an ancestor is a test runner; suppress it as a card when another task candidate is an ancestor. For each remaining root, aggregate non-helper descendants:

```python
root["cpu_number"] = sum(member["cpu_number"] for member in members)
root["rss_mb"] = sum(member["rss_mb"] for member in members)
root["worker_count"] = max(0, len(members) - 1)
root["gpu"] = any(member["gpu"] for member in members)
```

Keep the root PID, root command, root elapsed time, and current SSH user.

- [ ] **Step 4: Render worker count and aggregated resources**

The existing CPU and RAM fields use the aggregated values. Add a small `Workers N` badge only when `worker_count > 0`.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest -q tests/test_dashboard_myjobs.py tests/test_dashboard_html.py`

Expected: PASS.

### Task 2: Resolve logs safely without reading pipes

**Files:**
- Create: `tests/test_server_monitor_logs.py`
- Modify: `scripts/server_monitor.py`
- Modify: `scripts/web/dashboard.py`

- [ ] **Step 1: Write failing log-source tests**

Cover three inputs:

```python
resolve_log_source("/tmp/train.log", "")
# => {"kind": "file", "path": "/tmp/train.log"}

resolve_log_source("pipe:[123]", "__TEE__\ntee\t/tmp/train.log")
# => {"kind": "tee", "path": "/tmp/train.log"}

resolve_log_source("pipe:[123]", "")
# => {"kind": "unavailable", "path": ""}
```

Also fake the SSH command boundary and assert no generated command contains `tail ... /proc/<pid>/fd/1` or `fd/2`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/test_server_monitor_logs.py`

Expected: FAIL because the resolver does not exist and `parse_logs` currently tails `/proc/PID/fd/*`.

- [ ] **Step 3: Implement a shared safe resolver**

`inspect_log_source(ssh, pid)` reads fd symlinks and, for `pipe:[inode]`, scans only processes owned by `id -u` for a `tee` process whose stdin has the same pipe inode. Parse the first non-option `tee` destination and require an absolute regular-file path. Return:

```python
{"kind": "file" | "tee" | "unavailable", "path": str, "stdout_target": str,
 "stderr_target": str, "message": str}
```

All PID values use integer validation; all paths use shell quoting. Never read from a pipe, terminal, socket, or anonymous inode.

- [ ] **Step 4: Make polling and detail use the same source**

`parse_logs` tails at most 30 lines from the resolved regular file. `get_process_log` tails the same path for the requested line count and includes `log_source`, `log_path`, and `log_message` in `detail`. If the file is empty, return empty stdout plus the explicit message instead of silently guessing another recent log.

- [ ] **Step 5: Verify GREEN**

Run: `python -m pytest -q tests/test_server_monitor_logs.py tests/test_dashboard_security.py`

Expected: PASS and captured SSH commands contain no direct pipe tail.

### Task 3: Full verification and A40/AutoDL smoke test

**Files:**
- Modify only if a regression is found in the files above.

- [ ] **Step 1: Run the local suite**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run syntax and platform checks**

Run: `python -m compileall -q scripts tests`

Run: `$result = Invoke-Pester -Path tests\migration.Tests.ps1 -PassThru; if ($result.FailedCount -gt 0) { exit 1 }`

Run the existing Node syntax check for `scripts/web/dashboard.html`.

- [ ] **Step 3: Restart the Dashboard and verify A40**

Start with `start_dashboard.ps1`. Query `dev-server` and assert one root training card, aggregated worker count/resources, and detail metadata identifying the `tee` log destination. Poll repeatedly and compare the exact count of current-user commands matching `tail -30 /proc/[0-9]+/fd/[12]`; it must remain zero.

- [ ] **Step 4: Verify AutoDL regression behavior**

Query `seetacloud` detail for a live training PID. Its direct regular stdout log must still render.

- [ ] **Step 5: Commit and merge locally**

Stage only the plan, implementation, and tests. Commit on `codex/a40-process-log-fix`, fast-forward local `main`, rerun tests, restart the merged Dashboard, then remove the temporary worktree and branch. Do not stage `SKILL.md`, project `AGENTS.md`, credentials, known-host data, or logs.
