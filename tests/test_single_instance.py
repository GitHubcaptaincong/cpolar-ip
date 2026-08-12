import os
import unittest

from cpolar_notifier.single_instance import SingleInstance


@unittest.skipUnless(os.name == "nt", "Windows named-object test")
class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_signals_owner(self) -> None:
        name = f"CpolarNotifierTest.{os.getpid()}"
        owner = SingleInstance(name)
        second = SingleInstance(name)
        try:
            self.assertTrue(owner.acquire())
            self.assertFalse(second.acquire())
            self.assertTrue(second.signal("show"))
            self.assertTrue(owner.poll("show"))
            self.assertFalse(owner.poll("show"))
        finally:
            second.release()
            owner.release()


if __name__ == "__main__":
    unittest.main()
