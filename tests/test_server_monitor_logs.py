import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import server_monitor


class ServerMonitorLogTests(unittest.TestCase):
    def test_resolves_direct_stdout_file(self):
        source = server_monitor.resolve_log_source(
            "/tmp/train.log",
            "/tmp/train.log",
            "",
        )

        self.assertEqual(source["kind"], "file")
        self.assertEqual(source["path"], "/tmp/train.log")

    def test_resolves_tee_destination_for_pipe(self):
        source = server_monitor.resolve_log_source(
            "pipe:[123]",
            "pipe:[123]",
            "2392713\ttee /work/run/train.log",
        )

        self.assertEqual(source["kind"], "tee")
        self.assertEqual(source["path"], "/work/run/train.log")

    def test_unresolved_pipe_is_never_treated_as_a_file(self):
        source = server_monitor.resolve_log_source("pipe:[123]", "pipe:[123]", "")

        self.assertEqual(source["kind"], "unavailable")
        self.assertEqual(source["path"], "")

    def test_parse_logs_tails_resolved_tee_file_not_proc_pipe(self):
        commands = []

        def fake_cmd(_ssh, command, t=15):
            commands.append(command)
            if "__STDOUT__" in command:
                return "__STDOUT__\npipe:[123]\n__STDERR__\npipe:[123]\n"
            if "__PIPE_PEERS__" in command:
                return "2392713\ttee /work/run/train.log"
            if command.startswith("test -f"):
                return "OK"
            if command.startswith("tail -"):
                return "Epoch 4/20 loss=0.25"
            return ""

        with patch("server_monitor._cmd", side_effect=fake_cmd):
            info = server_monitor.parse_logs(object(), 2392712)

        self.assertEqual(info["epoch"], "4/20")
        self.assertEqual(info["loss"], 0.25)
        tail_commands = [command for command in commands if command.startswith("tail -")]
        self.assertEqual(len(tail_commands), 1)
        self.assertIn("/work/run/train.log", tail_commands[0])
        self.assertFalse(any("/proc/2392712/fd/" in command for command in tail_commands))


if __name__ == "__main__":
    unittest.main()
