import unittest
from pathlib import Path


class DashboardHtmlSafetyTests(unittest.TestCase):
    def test_server_strings_are_escaped_before_html_interpolation(self):
        html = (Path(__file__).parents[1] / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("+escHtml(g.name)+", html)
        self.assertIn("+escHtml(p.user)+", html)
        self.assertIn("+escHtml(n.iface)+", html)

    def test_myjobs_task_section_and_container_scope_are_rendered(self):
        html = (Path(__file__).parents[1] / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("my_tasks", html)
        self.assertIn("pid_scope", html)
        self.assertIn("vram_mb", html)
        self.assertIn("Container scope", html)


if __name__ == "__main__":
    unittest.main()
