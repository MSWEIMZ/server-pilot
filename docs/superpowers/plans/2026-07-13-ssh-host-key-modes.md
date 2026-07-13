# SSH Host Key Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add selectable SSH host-key policies while keeping `relaxed` as the default.

**Architecture:** `scripts/security.py` resolves one policy name and configures Paramiko accordingly. Existing connection entry points pass the resolved server policy into the shared connector; configuration examples and documentation describe global defaults and per-server override.

**Tech Stack:** Python 3, Paramiko, unittest.

---

### Task 1: Test policy selection

**Files:**
- Modify: `tests/test_security.py`
- Modify: `scripts/security.py`

- [ ] Write tests for default `relaxed`, `accept-new`, `strict`, and an invalid mode using a fake Paramiko client.
- [ ] Run `python -m unittest tests.test_security -v` and verify the new imports fail before implementation.
- [ ] Implement `normalize_host_key_policy` and policy-specific client setup; default missing values to `relaxed`.
- [ ] Re-run the test module and commit the policy implementation.

### Task 2: Pass configuration into shared connections

**Files:**
- Modify: `scripts/server_monitor.py`
- Modify: `scripts/ssh_exec.py`
- Modify: `scripts/file_ops.py`
- Modify: `scripts/task_mgr.py`
- Modify: `scripts/upload_and_run.py`

- [ ] Add the resolved `host_key_policy` value to every shared SSH connection call without changing command interfaces.
- [ ] Run the complete Python test suite and syntax compilation.

### Task 3: Document and verify

**Files:**
- Modify: `scripts/server_config.example.json`
- Modify: `README.md`
- Modify: `README_CN.md`

- [ ] Document global default and per-server override, with `relaxed` as default.
- [ ] Run `python -m unittest discover -s tests -v` and `python -m compileall -q scripts`.
- [ ] Commit only files belonging to this feature; preserve unrelated working-tree changes.
