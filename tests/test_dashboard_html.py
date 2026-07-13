import unittest
from pathlib import Path


class DashboardHtmlSafetyTests(unittest.TestCase):
    def test_server_strings_are_escaped_before_html_interpolation(self):
        html = (Path(__file__).parents[1] / "scripts" / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("+escHtml(g.name)+", html)
        self.assertIn("+escHtml(p.user)+", html)
        self.assertIn("+escHtml(n.iface)+", html)


if __name__ == "__main__":
    unittest.main()
