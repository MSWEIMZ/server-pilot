import unittest
from unittest.mock import patch

from scripts.web.dashboard import (
    get_procs,
    parse_myjobs_output,
    parse_own_tasks_output,
    scope_myjobs,
    training_from_myjobs,
)


MYJOBS_SAMPLE = """
\x1b[1mmyjobs -d (user=testuser)\x1b[0m
GPU:
cuda:0 4096/46068M 100% 41972M free
Users:
User Procs CPU RAM Tasks
wanghon+ 2 120% 3.0G train.py×2
licheng+ 1 80% 2.0G train_SAM.py×1
testuse+ 1 70% 1.0G train_own.py×1
Processes:
User PID CPU RAM Task Start Run
─────────────────────────────────────────────────────────────────────────────
wanghon+ 1681573 99% 2.0G train.py 10:00 6m
licheng+ 1505191 88% 1.5G train_SAM.py 09:00 1h
testuse+ 1700000 70% 1.0G train_own.py 09:30 30m
My Tasks:
Group Count CPU RAM
other 1 2% 11M
nvitop 2 0% 6M
✓ total 3
"""

OWN_PS_SAMPLE = """
__CURRENT_USER__ testuser
__PROCESSES__
1700000 1 testuse+ 70.0 0.4 1048576 1800 python train_own.py --epochs 20
1700001 1 testuse+ 10.0 0.2 524288 600 /opt/tools/custom-worker --job experiment-a
1700002 1 testuse+ 0.0 0.0 776 10000 /home/testuser/.vscode-server/code agent host
1700003 1 testuse+ 0.0 0.0 6000 300 nvitop
1700004 1 testuse+ 0.0 0.0 3000 0 ps -u 2102
1700005 1700000 testuse+ 30.0 0.4 1048576 1700 python train_own.py --epochs 20
1700006 1 testuse+ 0.0 0.0 1024 1700 tee /work/logs/train.log
1700007 1 testuse+ 90.0 0.5 1048576 120 python -m unittest experiments.ch2.exp64.test_v02_training_contract
__GPU_PIDS__
1700000
1700005
"""


class DashboardMyjobsTests(unittest.TestCase):
    def test_top_processes_query_is_limited_to_current_uid(self):
        output = "USER PID %CPU %MEM COMMAND\ntestuse+ 1700000 70.0 0.4 python train.py"
        with patch("scripts.web.dashboard._cmd", return_value=output) as command:
            processes = get_procs(object())

        self.assertIn('ps -u "$(id -u)"', command.call_args.args[1])
        self.assertEqual([process["user"] for process in processes], ["testuse+"])

    def test_parse_preserves_users_processes_and_my_tasks(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)

        self.assertEqual(parsed["current_user"], "testuser")
        self.assertEqual(parsed["scope"], "container")
        self.assertEqual(parsed["data_source"], "myjobs")
        self.assertEqual([p["user"] for p in parsed["processes"]], ["wanghon+", "licheng+", "testuse+"])
        self.assertEqual([t["group"] for t in parsed["my_tasks"]], ["other", "nvitop"])

    def test_default_scope_keeps_only_current_user_with_truncated_ps_name(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)
        scoped = scope_myjobs(parsed)
        training = training_from_myjobs(scoped)

        self.assertEqual(scoped["process_scope"], "self")
        self.assertEqual(scoped["total_visible_processes"], 3)
        self.assertEqual([p["user"] for p in scoped["processes"]], ["testuse+"])
        self.assertEqual([u["user"] for u in scoped["users"]], ["testuse+"])
        self.assertEqual(len(training), 1)
        self.assertEqual(training[0]["user"], "testuse+")
        self.assertEqual(training[0]["pid_scope"], "container")
        self.assertIsNone(training[0]["host_pid"])
        self.assertIsNone(training[0]["vram_mb"])
        self.assertIsNone(training[0]["vram"])
        self.assertEqual(training[0]["ram_mb"], 1024)
        self.assertEqual(training[0]["cpu"], 70)

    def test_all_scope_requires_explicit_opt_in(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)
        scoped = scope_myjobs(parsed, "all")

        self.assertEqual(scoped["process_scope"], "all")
        self.assertEqual(len(training_from_myjobs(scoped)), 3)

    def test_native_collector_keeps_only_real_current_user_tasks(self):
        info = parse_own_tasks_output(OWN_PS_SAMPLE)

        self.assertEqual(info["current_user"], "testuser")
        self.assertEqual(info["data_source"], "ps-proc")
        self.assertEqual(info["process_scope"], "self")
        self.assertEqual([p["pid"] for p in info["processes"]], ["1700000", "1700001"])
        self.assertTrue(info["processes"][0]["gpu"])
        self.assertEqual(len(training_from_myjobs(info)), 2)

    def test_verified_same_namespace_vram_is_preserved(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)
        scoped = scope_myjobs(parsed)
        training = training_from_myjobs(scoped, [{"pid": 1700000, "vram": 1234}])

        self.assertEqual(training[0]["vram_mb"], 1234)
        self.assertEqual(training[0]["vram_source"], "nvidia-smi")


if __name__ == "__main__":
    unittest.main()
