import tempfile
import unittest
from pathlib import Path

from cpolar_notifier.config import ConfigStore, parse_cpolar_config


class CpolarConfigTests(unittest.TestCase):
    def test_parse_tunnels_without_returning_account_fields(self) -> None:
        definitions = parse_cpolar_config(
            """
authtoken: should-not-be-loaded
tunnels:
  website:
    id: abc
    proto: http
    addr: "8080"
    region: cn_top
    start_type: enable
  remoteDesktop:
    proto: tcp
    addr: 3389
    start_type: disable
email: user@example.com
"""
        )
        self.assertEqual(set(definitions), {"website", "remoteDesktop"})
        self.assertEqual(definitions["website"].local_addr, "8080")
        self.assertEqual(definitions["website"].region, "cn_top")
        self.assertTrue(definitions["website"].start_enabled)
        self.assertFalse(definitions["remoteDesktop"].start_enabled)

    def test_config_store_merges_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(Path(directory))
            store.save({"interval_seconds": 30, "selected_tunnels": ["ssh"]})
            loaded = store.load()
            self.assertEqual(loaded["interval_seconds"], 30)
            self.assertEqual(loaded["selected_tunnels"], ["ssh"])
            self.assertIn("serverchan", loaded["providers"])


if __name__ == "__main__":
    unittest.main()
