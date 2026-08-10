import unittest
from unittest.mock import patch

from scripts import security

from scripts.security import (
    clamp_log_lines,
    configure_host_key_policy,
    normalize_host_key_policy,
    project_known_hosts_path,
    quote_remote_path,
    validate_pid,
)


class SecurityPrimitiveTests(unittest.TestCase):
    def test_accepts_positive_decimal_pid(self):
        self.assertEqual(validate_pid("123"), 123)

    def test_rejects_non_decimal_pid(self):
        with self.assertRaises(ValueError):
            validate_pid("1;id")

    def test_clamps_log_lines_to_safe_range(self):
        self.assertEqual(clamp_log_lines("0"), 1)
        self.assertEqual(clamp_log_lines("200"), 200)
        self.assertEqual(clamp_log_lines("999999"), 1000)

    def test_quotes_posix_shell_path(self):
        self.assertEqual(quote_remote_path("/tmp/a'b.log"), "'/tmp/a'\"'\"'b.log'")

    def test_configures_reject_policy_and_project_known_hosts(self):
        class Client:
            def __init__(self):
                self.system_loaded = False
                self.host_keys = []
                self.policy = None

            def load_system_host_keys(self):
                self.system_loaded = True

            def load_host_keys(self, path):
                self.host_keys.append(path)

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

        class Paramiko:
            class RejectPolicy:
                pass

        client = Client()
        configure_host_key_policy(client, Paramiko, "strict")
        self.assertTrue(client.system_loaded)
        self.assertIsInstance(client.policy, Paramiko.RejectPolicy)
        self.assertTrue(str(project_known_hosts_path()).endswith("scripts\\known_hosts"))

    def test_defaults_to_strict_host_key_policy(self):
        self.assertEqual(normalize_host_key_policy(None), "strict")

    def test_configures_each_host_key_policy(self):
        class Client:
            def load_system_host_keys(self):
                pass

            def load_host_keys(self, path):
                pass

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

        class Paramiko:
            class AutoAddPolicy:
                pass
            class RejectPolicy:
                pass

            class MissingHostKeyPolicy:
                pass

        expected = {
            "accept-new": "AcceptNewPolicy",
            "strict": "RejectPolicy",
        }
        for mode, policy_name in expected.items():
            client = Client()
            configure_host_key_policy(client, Paramiko, mode)
            self.assertEqual(type(client.policy).__name__, policy_name)

    def test_rejects_unknown_host_key_policy(self):
        with self.assertRaises(ValueError):
            normalize_host_key_policy("unsupported")

    def test_password_auth_takes_priority_over_auto_discovered_key(self):
        class Client:
            def load_system_host_keys(self):
                pass

            def load_host_keys(self, path):
                pass

            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, **kwargs):
                self.kwargs = kwargs

            def get_transport(self):
                return None

            def close(self):
                pass

        class Paramiko:
            class AutoAddPolicy:
                pass

            class RejectPolicy:
                pass

            class MissingHostKeyPolicy:
                pass

            def __init__(self):
                self.client = Client()

            def SSHClient(self):
                return self.client

        paramiko = Paramiko()
        with patch.object(security, "require_paramiko", return_value=paramiko), \
             patch.object(
                 security,
                 "_key_filename",
                 side_effect=lambda key_file, include_defaults=True: "C:/fake/id_rsa" if include_defaults else None,
             ):
            security.connect_ssh(
                "example.test", 22, "root", password="configured-password", key_file="", retries=1
            )

        self.assertIn("password", paramiko.client.kwargs)
        self.assertEqual(paramiko.client.kwargs["password"], "configured-password")
        self.assertNotIn("key_filename", paramiko.client.kwargs)


if __name__ == "__main__":
    unittest.main()
