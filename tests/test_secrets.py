import os
import tempfile
import unittest
from pathlib import Path

from cpolar_notifier.secrets import SecretStore


@unittest.skipUnless(os.name == "nt", "DPAPI test only runs on Windows")
class SecretStoreTests(unittest.TestCase):
    def test_round_trip_is_encrypted_at_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SecretStore(Path(directory))
            store.set("telegram.bot_token", "very-secret-token")
            self.assertEqual(store.get("telegram.bot_token"), "very-secret-token")
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn("very-secret-token", raw)


if __name__ == "__main__":
    unittest.main()
