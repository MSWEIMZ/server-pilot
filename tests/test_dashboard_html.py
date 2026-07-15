import unittest
from pathlib import Path


class DashboardHtmlSafetyTests(unittest.TestCase):
    def test_server_strings_are_escaped_before_html_interpolation(self):
        html = (Path(__file__).parents[1] / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("+escHtml(g.name)+", html)
        self.assertIn("+escHtml(p.user)+", html)
        self.assertIn("+escHtml(n.iface)+", html)

    def test_current_user_training_and_gpu_occupancy_are_distinct(self):
        html = (Path(__file__).parents[1] / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("pid_scope", html)
        self.assertIn("vram_mb", html)
        self.assertIn("gpu_occupancy", html)
        self.assertIn("GPU Occupancy", html)
        self.assertIn("mapping_status", html)
        self.assertNotIn('id="clusterSection"', html)
        self.assertNotIn("renderMyjobs(d.myjobs)", html)


if __name__ == "__main__":
    unittest.main()
