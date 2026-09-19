"""Regression tests for SSH command-channel timeout handling.

These cover the bug where ``exec_command(timeout=30)`` aborted any remote
command that produced no output for 30s, and where the resulting
``socket.timeout`` was reported as a bare, empty ``SSH Error:``.
"""
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import io

import security
import server_monitor
import ssh_exec


def _import_file_ops():
    """Import file_ops without letting its legacy console rewrap escape.

    file_ops replaces sys.stdout/sys.stderr with TextIOWrapper objects at
    import time; under pytest that closes the capture buffer on teardown.
    Import it against throwaway streams instead.
    """
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        import file_ops  # noqa: F401
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return file_ops


class FakeChannel:
    def __init__(self, out=b"", err=b"", exit_code=0, exited=True):
        self._out = bytearray(out)
        self._err = bytearray(err)
        self.exit_code = exit_code
        self.exited = exited
        self.timeout = None

    def recv_ready(self):
        return bool(self._out)

    def recv(self, n):
        chunk = bytes(self._out[:n])
        del self._out[:n]
        return chunk

    def recv_stderr_ready(self):
        return bool(self._err)

    def recv_stderr(self, n):
        chunk = bytes(self._err[:n])
        del self._err[:n]
        return chunk

    def exit_status_ready(self):
        return self.exited and not self._out and not self._err

    def recv_exit_status(self):
        return self.exit_code


class FakeStdout:
    def __init__(self, channel):
        self.channel = channel


class FakeSSH:
    """Minimal paramiko.SSHClient stand-in for exec_remote()."""

    def __init__(self, out=b"", err=b"", exit_code=0, exited=True):
        self.channel = FakeChannel(out, err, exit_code, exited)
        self.stdout = FakeStdout(self.channel)
        self.stderr = object()
        self.timeouts = []
        self.commands = []

    def exec_command(self, command, timeout=None, get_pty=False):
        self.commands.append(command)
        self.timeouts.append(timeout)
        return None, self.stdout, self.stderr

    def close(self):
        self.closed = True


class RaisingSSH:
    def __init__(self, exc):
        self.exc = exc

    def exec_command(self, command, timeout=None, get_pty=False):
        raise self.exc


class TimeoutSemanticsTests(unittest.TestCase):
    def test_exec_remote_defaults_to_no_read_timeout(self):
        ssh = FakeSSH(out=b"long output\n")
        out, err, code = security.exec_remote(ssh, "sleep 40")
        self.assertEqual(ssh.timeouts, [None])
        self.assertEqual(out, "long output\n")
        self.assertEqual(err, "")
        self.assertEqual(code, 0)

    def test_exec_remote_zero_means_unlimited_not_immediate_timeout(self):
        ssh = FakeSSH()
        security.exec_remote(ssh, "sleep 40", read_timeout=0)
        self.assertEqual(ssh.timeouts, [None])

    def test_exec_remote_forwards_explicit_read_timeout(self):
        ssh = FakeSSH()
        security.exec_remote(ssh, "cmd", read_timeout=120)
        self.assertEqual(ssh.timeouts, [120.0])

    def test_exec_remote_collects_stdout_and_stderr_and_exit_code(self):
        ssh = FakeSSH(out=b"out\n", err=b"err\n", exit_code=3)
        out, err, code = security.exec_remote(ssh, "cmd")
        self.assertEqual(out, "out\n")
        self.assertEqual(err, "err\n")
        self.assertEqual(code, 3)

    def test_env_override_applies_to_read_timeout(self):
        ssh = FakeSSH()
        with patch.dict(os.environ, {"SP_READ_TIMEOUT": "45"}):
            security.exec_remote(ssh, "cmd")
        self.assertEqual(ssh.timeouts, [45.0])

    def test_env_override_none_disables_timeout(self):
        ssh = FakeSSH()
        with patch.dict(os.environ, {"SP_READ_TIMEOUT": "none"}):
            security.exec_remote(ssh, "cmd", read_timeout=5)
        self.assertEqual(ssh.timeouts, [None])

    def test_bounded_read_timeout_fails_loudly(self):
        ssh = FakeSSH(exited=False)
        with self.assertRaises(TimeoutError) as ctx:
            security.exec_remote(ssh, "cmd", read_timeout=0.01)
        self.assertIn("no channel data", str(ctx.exception))

    def test_normalize_read_timeout_rejects_garbage(self):
        with self.assertRaises(ValueError):
            security.normalize_read_timeout("soon")


class SlowChatterChannel(FakeChannel):
    """Emits a chunk every ~20ms for ``total_seconds``, then exits 0."""

    def __init__(self, total_seconds=0.6, chunk=b"tick\n"):
        super().__init__()
        self.deadline = time.monotonic() + total_seconds
        self.chunk = chunk

    def recv_ready(self):
        return time.monotonic() < self.deadline

    def recv(self, n):
        time.sleep(0.02)
        return self.chunk

    def exit_status_ready(self):
        return time.monotonic() >= self.deadline


class IdleTimeoutSemanticsTests(unittest.TestCase):
    """A read timeout must bound idleness, not total wall time."""

    def test_chatty_command_outliving_timeout_is_not_killed(self):
        ssh = FakeSSH()
        channel = SlowChatterChannel(total_seconds=0.6)
        ssh.channel = channel
        ssh.stdout = FakeStdout(channel)
        out, err, code = security.exec_remote(ssh, "chatty", read_timeout=0.2)
        self.assertEqual(code, 0)
        self.assertGreater(len(out), 0)


class ErrorReportingTests(unittest.TestCase):
    def test_describe_error_is_never_empty_for_socket_timeout(self):
        message = security.describe_error(socket.timeout())
        self.assertTrue(message.strip())
        self.assertIn("timeout", message.lower())

    def test_describe_error_keeps_type_and_message(self):
        message = security.describe_error(RuntimeError("boom"))
        self.assertIn("RuntimeError", message)
        self.assertIn("boom", message)

    def test_ssh_exec_run_command_does_not_cap_read_timeout(self):
        ssh = FakeSSH(out=b"done\n")
        with patch.object(ssh_exec, "connect_ssh", return_value=ssh):
            result = ssh_exec.run_command(
                "host", 22, "user", "pw", None, "sleep 40",
            )
        self.assertEqual(ssh.timeouts, [None])
        self.assertEqual(result["stdout"], "done\n")
        self.assertEqual(result["exit_code"], 0)

    def test_ssh_exec_timeout_flag_parsing(self):
        self.assertIsNone(ssh_exec.parse_timeout_arg("none"))
        self.assertIsNone(ssh_exec.parse_timeout_arg("0"))
        self.assertEqual(ssh_exec.parse_timeout_arg("120"), 120.0)

    def test_server_monitor_records_probe_failure_instead_of_silence(self):
        server_monitor.take_command_errors()
        ssh = RaisingSSH(RuntimeError("channel closed"))
        self.assertEqual(server_monitor._cmd(ssh, "nvidia-smi"), "")
        errors = server_monitor.take_command_errors()
        self.assertEqual(len(errors), 1)
        self.assertIn("channel closed", errors[0]["error"])
        self.assertIn("nvidia-smi", errors[0]["command"])
        self.assertEqual(server_monitor.take_command_errors(), [])

    def test_file_ops_reports_error_text_instead_of_empty_message(self):
        file_ops = _import_file_ops()
        ssh = RaisingSSH(socket.timeout())
        message = file_ops._cmd(ssh, "ls /tmp")
        self.assertTrue(message.startswith("Error: "))
        self.assertGreater(len(message), len("Error: "))


if __name__ == "__main__":
    unittest.main()
