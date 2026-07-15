import json
import tempfile
import unittest
from pathlib import Path

from scripts.web import dashboard


SAMPLE_CONFIG = {
    "defaults": {"username": "root"},
    "servers": {
        "seetacloud": {
            "host": "first.example",
            "port": 22,
            "password": "secret-one",
        },
        "dev-server": {
            "host": "second.example",
            "port": 2200,
            "username": "alice",
            "key_file": "/private/key",
        },
        "westb-seetacloud": {
            "host": "unused.example",
            "port": 22,
            "password": "secret-two",
        },
    },
}


class DashboardServerManagementTests(unittest.TestCase):
    def test_public_server_list_redacts_credentials(self):
        rows = dashboard.public_server_configs(SAMPLE_CONFIG)
        encoded = json.dumps(rows)

        self.assertEqual([row["name"] for row in rows], [
            "seetacloud",
            "dev-server",
            "westb-seetacloud",
        ])
        self.assertNotIn("secret-one", encoded)
        self.assertNotIn("secret-two", encoded)
        self.assertNotIn("/private/key", encoded)
        self.assertEqual(rows[0]["auth"], "password")
        self.assertEqual(rows[1]["auth"], "key")

    def test_delete_removes_only_named_server_without_mutating_input(self):
        updated = dashboard.delete_server_config(SAMPLE_CONFIG, "westb-seetacloud")

        self.assertEqual(list(updated["servers"]), ["seetacloud", "dev-server"])
        self.assertIn("westb-seetacloud", SAMPLE_CONFIG["servers"])

    def test_delete_rejects_missing_server(self):
        with self.assertRaisesRegex(ValueError, "server not found"):
            dashboard.delete_server_config(SAMPLE_CONFIG, "missing")

    def test_add_validates_and_preserves_existing_servers(self):
        updated = dashboard.add_server_config(SAMPLE_CONFIG, {
            "name": "new-gpu",
            "host": "gpu.example",
            "port": 2222,
            "username": "bob",
            "password": "new-secret",
            "host_key_policy": "off",
        })

        self.assertEqual(len(updated["servers"]), 4)
        self.assertEqual(updated["servers"]["new-gpu"]["port"], 2222)
        self.assertEqual(updated["servers"]["new-gpu"]["password"], "new-secret")
        with self.assertRaisesRegex(ValueError, "server already exists"):
            dashboard.add_server_config(SAMPLE_CONFIG, {"name": "dev-server", "host": "x"})

    def test_save_writes_valid_json_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server_config.json"
            dashboard.save_server_config(SAMPLE_CONFIG, path)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), SAMPLE_CONFIG)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_dashboard_links_to_management_page(self):
        root = Path(__file__).parents[1]
        dashboard_html = (root / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        manager_path = root / "scripts" / "web" / "server_manager.html"

        self.assertIn('href="/manage"', dashboard_html)
        self.assertTrue(manager_path.exists())
        manager_html = manager_path.read_text(encoding="utf-8")
        self.assertIn("/api/server-config", manager_html)
        self.assertIn('method:"DELETE"', manager_html)
        self.assertIn('method:"POST"', manager_html)


if __name__ == "__main__":
    unittest.main()
