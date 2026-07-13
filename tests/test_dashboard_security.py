import unittest

from scripts.web.dashboard import dashboard_settings, parse_log_request, request_is_authorized


class DashboardSecurityTests(unittest.TestCase):
    def test_defaults_to_loopback_without_token(self):
        settings = dashboard_settings()
        self.assertEqual(settings.bind, "127.0.0.1")
        self.assertIsNone(settings.token)

    def test_remote_bind_requires_explicit_flag_and_token(self):
        with self.assertRaises(ValueError):
            dashboard_settings(bind="0.0.0.0")
        with self.assertRaises(ValueError):
            dashboard_settings(bind="0.0.0.0", allow_remote=True)
        settings = dashboard_settings(bind="0.0.0.0", allow_remote=True, token="test-token")
        self.assertEqual(settings.bind, "0.0.0.0")

    def test_remote_api_requires_bearer_token(self):
        settings = dashboard_settings(bind="0.0.0.0", allow_remote=True, token="test-token")
        self.assertFalse(request_is_authorized({}, settings))
        self.assertFalse(request_is_authorized({"Authorization": "Bearer wrong"}, settings))
        self.assertTrue(request_is_authorized({"Authorization": "Bearer test-token"}, settings))

    def test_log_request_validates_pid_and_clamps_lines(self):
        self.assertEqual(parse_log_request({"pid": ["19"], "lines": ["999999"]}), (19, 1000))
        with self.assertRaises(ValueError):
            parse_log_request({"pid": ["1;id"]})


if __name__ == "__main__":
    unittest.main()
