import unittest
from unittest.mock import patch

from scripts.web import dashboard


PROCESS_INFO = """
__CMDLINE__
python -m experiments.demo.train
__CWD__
/work/experiment
__FD__
12
__STATUS__
Name:\tpython
State:\tS (sleeping)
__IO__
read_bytes: 0
__FDLINKS__
__STDOUT__
pipe:[123]
__STDERR__
pipe:[123]
__END__
"""


class DashboardLogTests(unittest.TestCase):
    def test_detail_uses_shared_safe_tee_source(self):
        commands = []

        def fake_cmd(_ssh, command, t=15):
            commands.append(command)
            if "__CMDLINE__" in command:
                return PROCESS_INFO
            return ""

        source = {
            "kind": "tee",
            "path": "/work/experiment/train.log",
            "message": "stdout pipe is written by tee",
            "stdout_target": "pipe:[123]",
            "stderr_target": "pipe:[123]",
        }
        with patch("scripts.web.dashboard._cmd", side_effect=fake_cmd), patch(
            "scripts.web.dashboard.tail_process_log",
            create=True,
            return_value=("Epoch 4/20 loss=0.25", source),
        ) as safe_tail:
            result = dashboard.get_process_log(object(), 2392712, 100)

        safe_tail.assert_called_once()
        self.assertIn("Epoch 4/20", result["stdout"])
        self.assertEqual(result["detail"]["log_source"], "tee")
        self.assertEqual(result["detail"]["log_path"], "/work/experiment/train.log")
        self.assertFalse(any("tail" in command and "/proc/2392712/fd/" in command for command in commands))

    def test_empty_tee_file_returns_explanatory_message(self):
        source = {
            "kind": "tee",
            "path": "/work/experiment/train.log",
            "message": "log file exists but is currently empty",
            "stdout_target": "pipe:[123]",
            "stderr_target": "pipe:[123]",
        }
        with patch("scripts.web.dashboard._cmd", return_value=PROCESS_INFO), patch(
            "scripts.web.dashboard.tail_process_log",
            create=True,
            return_value=("", source),
        ):
            result = dashboard.get_process_log(object(), 2392712, 100)

        self.assertEqual(result["stdout"], "")
        self.assertEqual(result["detail"]["log_message"], "log file exists but is currently empty")


if __name__ == "__main__":
    unittest.main()
