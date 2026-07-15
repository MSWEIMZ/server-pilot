import unittest

from scripts.web.dashboard import parse_myjobs_output, training_from_myjobs


MYJOBS_SAMPLE = """
\x1b[1mmyjobs -d (user=testuser)\x1b[0m
GPU:
cuda:0 4096/46068M 100% 41972M free
Users:
User Procs CPU RAM Tasks
wanghon+ 2 120% 3.0G train.py×2
licheng+ 1 80% 2.0G train_SAM.py×1
Processes:
User PID CPU RAM Task Start Run
─────────────────────────────────────────────────────────────────────────────
wanghon+ 1681573 99% 2.0G train.py 10:00 6m
licheng+ 1505191 88% 1.5G train_SAM.py 09:00 1h
My Tasks:
Group Count CPU RAM
other 1 2% 11M
nvitop 2 0% 6M
✓ total 3
"""


class DashboardMyjobsTests(unittest.TestCase):
    def test_parse_preserves_users_processes_and_my_tasks(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)

        self.assertEqual(parsed["current_user"], "testuser")
        self.assertEqual(parsed["scope"], "container")
        self.assertEqual(parsed["data_source"], "myjobs")
        self.assertEqual([p["user"] for p in parsed["processes"]], ["wanghon+", "licheng+"])
        self.assertEqual([t["group"] for t in parsed["my_tasks"]], ["other", "nvitop"])

    def test_training_fallback_does_not_filter_or_fake_vram(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)
        training = training_from_myjobs(parsed)

        self.assertEqual(len(training), 2)
        self.assertEqual({p["user"] for p in training}, {"wanghon+", "licheng+"})
        self.assertEqual(training[0]["pid_scope"], "container")
        self.assertIsNone(training[0]["host_pid"])
        self.assertIsNone(training[0]["vram_mb"])
        self.assertIsNone(training[0]["vram"])
        self.assertEqual(training[0]["ram_mb"], 2048)
        self.assertEqual(training[0]["cpu"], 99)

    def test_verified_same_namespace_vram_is_preserved(self):
        parsed = parse_myjobs_output(MYJOBS_SAMPLE)
        training = training_from_myjobs(parsed, [{"pid": 1681573, "vram": 1234}])

        self.assertEqual(training[0]["vram_mb"], 1234)
        self.assertEqual(training[0]["vram_source"], "nvidia-smi")


if __name__ == "__main__":
    unittest.main()
