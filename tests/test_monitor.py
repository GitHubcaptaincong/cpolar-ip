import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpolar_notifier.models import TunnelInfo
from cpolar_notifier.monitor import MonitorEngine, StateStore
from cpolar_notifier.notifications import NotificationResult, serverchan_endpoint


class FakeDiscovery:
    def __init__(self, tunnel: TunnelInfo) -> None:
        self.tunnel = tunnel

    def discover(self):
        return [self.tunnel]


class FakeProvider:
    name = "serverchan"
    display_name = "fake"

    def __init__(self) -> None:
        self.calls = []

    def send(self, title: str, body: str) -> NotificationResult:
        self.calls.append((title, body))
        return NotificationResult(self.name, True, "ok")


class MonitorTests(unittest.TestCase):
    def test_change_is_notified_once_then_notified_again_after_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tunnel = TunnelInfo(
                name="remoteDesktop",
                proto="tcp",
                active=True,
                public_urls={"tcp": "tcp://one.example:1000"},
            )
            discovery = FakeDiscovery(tunnel)
            provider = FakeProvider()
            engine = MonitorEngine(
                discovery,  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                StateStore(Path(directory)),
            )
            config = {
                "selected_tunnels": ["remoteDesktop"],
                "notify_on_first_seen": True,
                "providers": {"serverchan": {"enabled": True}},
            }
            with patch("cpolar_notifier.monitor.build_provider", return_value=provider):
                first = engine.check_once(config)
                second = engine.check_once(config)
                tunnel.public_urls = {"tcp": "tcp://two.example:2000"}
                third = engine.check_once(config)

            self.assertEqual(len(first.notifications), 1)
            self.assertEqual(len(second.notifications), 0)
            self.assertEqual(len(third.notifications), 1)
            self.assertEqual(len(provider.calls), 2)
            self.assertIn("tcp://one.example:1000", provider.calls[1][1])
            self.assertIn("tcp://two.example:2000", provider.calls[1][1])

    def test_serverchan_endpoint_supports_both_official_key_formats(self) -> None:
        self.assertEqual(
            serverchan_endpoint("SCT123"), "https://sctapi.ftqq.com/SCT123.send"
        )
        self.assertEqual(
            serverchan_endpoint("sctp42tabc"),
            "https://42.push.ft07.com/send/sctp42tabc.send",
        )


if __name__ == "__main__":
    unittest.main()
