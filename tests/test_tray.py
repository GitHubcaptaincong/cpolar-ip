import os
import queue
import time
import unittest

from cpolar_notifier.tray import WindowsTray


@unittest.skipUnless(os.name == "nt", "Windows notification-area test")
class WindowsTrayTests(unittest.TestCase):
    def test_tray_lifecycle_and_status_updates(self) -> None:
        commands: "queue.Queue[str]" = queue.Queue()
        tray = WindowsTray(commands)
        try:
            self.assertTrue(tray.start(), tray.error)
            self.assertTrue(tray.supported)
            tray.update("healthy", "cpolar test: healthy")
            time.sleep(0.05)
            tray.update("paused", "cpolar test: paused")
            time.sleep(0.05)
            tray.update("error", "cpolar test: error")
        finally:
            tray.close()
        self.assertFalse(tray.supported)


if __name__ == "__main__":
    unittest.main()
