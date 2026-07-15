import unittest

from scripts.web.dashboard import _extract_experiment, parse_gpu_occupancy_output


OCCUPANCY_SAMPLE = """
__GPUS__
0,GPU-aaa
1,GPU-bbb
__GPU_PROCESSES__
GPU-aaa,100,[Not Found],10240
GPU-bbb,200,[Not Found],20480
__CONTAINER_PROCESSES__
alice+ 1000 1 99.0 1048576 3600 python train.py --config /work/configs/experiment_a.yaml
alice+ 1001 1000 10.0 524288 3000 python train.py --config /work/configs/experiment_a.yaml
bob+ 2000 1 100.0 2097152 7200 python advanced_train.py --save-dir /work/checkpoints
bob+ 2001 1 0.0 120000 5000 python watch_jobs.py
carol+ 3000 1 90.0 1048576 120 python -m unittest experiments.ch2.exp64.test_v02_training_contract
"""


class DashboardGpuOccupancyTests(unittest.TestCase):
    def test_extracts_python_module_as_experiment_name(self):
        command = "python -m experiments.ch2.exp64.train_v02_single_branch"
        self.assertEqual(
            _extract_experiment(command),
            "experiments.ch2.exp64.train_v02_single_branch",
        )

    def test_maps_exact_vram_to_container_experiments_when_counts_match(self):
        result = parse_gpu_occupancy_output(OCCUPANCY_SAMPLE)

        self.assertEqual(result["meta"]["mapping_status"], "inferred-order")
        self.assertEqual(result["meta"]["host_process_count"], 2)
        self.assertEqual(result["meta"]["container_candidate_count"], 2)
        self.assertEqual(
            [(row["user"], row["gpu_index"], row["experiment"], row["vram_mb"]) for row in result["rows"]],
            [
                ("alice+", 0, "experiment_a.yaml", 10240),
                ("bob+", 1, "advanced_train.py", 20480),
            ],
        )
        self.assertEqual(result["rows"][0]["container_pid"], 1000)
        self.assertEqual(result["rows"][0]["host_pid"], 100)

    def test_does_not_guess_identity_when_candidate_counts_differ(self):
        mismatched = OCCUPANCY_SAMPLE.replace(
            "bob+ 2000 1 100.0 2097152 7200 python advanced_train.py --save-dir /work/checkpoints\n",
            "",
        )
        result = parse_gpu_occupancy_output(mismatched)

        self.assertEqual(result["meta"]["mapping_status"], "unavailable")
        self.assertEqual(len(result["rows"]), 2)
        self.assertIsNone(result["rows"][0]["user"])
        self.assertIsNone(result["rows"][0]["experiment"])
        self.assertIsNone(result["rows"][0]["container_pid"])


if __name__ == "__main__":
    unittest.main()
