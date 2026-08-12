import unittest
from unittest.mock import MagicMock, patch

from cpolar_notifier import app


class AppSingleInstanceRoutingTests(unittest.TestCase):
    def _assert_second_launch_command(self, arguments: list[str], command: str) -> None:
        instance = MagicMock()
        instance.acquire.return_value = False
        with patch(
            "cpolar_notifier.app.SingleInstance", return_value=instance
        ), patch("cpolar_notifier.app.configure_logging"):
            self.assertEqual(app.main(arguments), 0)
        instance.signal.assert_called_once_with(command)
        instance.release.assert_called_once()

    def test_normal_second_launch_shows_existing_window(self) -> None:
        self._assert_second_launch_command([], "show")

    def test_headless_second_launch_starts_existing_listener(self) -> None:
        self._assert_second_launch_command(["--headless"], "start")

    def test_exit_command_closes_existing_instance(self) -> None:
        self._assert_second_launch_command(["--exit"], "exit")

    def test_check_command_is_forwarded_to_existing_instance(self) -> None:
        self._assert_second_launch_command(["--check-once"], "check")


if __name__ == "__main__":
    unittest.main()
