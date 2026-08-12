import json
import tempfile
import unittest
from pathlib import Path

from cpolar_notifier.discovery import TunnelDiscovery
from cpolar_notifier.log_parser import parse_log_line


def log_line(packet: dict, timestamp: str = "2026-08-11T18:33:20+08:00") -> str:
    message = "[test] Read message " + json.dumps(packet, separators=(",", ":"))
    return f'time="{timestamp}" level=debug msg={json.dumps(message)}\n'


def start_packet(name: str, urls: list[tuple[str, str]]) -> dict:
    return {
        "Type": "RespStartTunnel",
        "Payload": {
            "TunnelItems": [
                {"TunnelName": name, "Protocol": protocol, "Url": url}
                for protocol, url in urls
            ],
            "StatusCode": 0,
        },
    }


class LogParserTests(unittest.TestCase):
    def test_parses_start_and_stop_events(self) -> None:
        events = parse_log_line(
            log_line(
                start_packet(
                    "website",
                    [
                        ("http", "http://sample.cpolar.top"),
                        ("https", "https://sample.cpolar.top"),
                    ],
                )
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "website")
        self.assertEqual(events[0].public_urls["https"], "https://sample.cpolar.top")
        self.assertTrue(events[0].active)

        stop = parse_log_line(
            log_line({"Type": "RespStopTunnel", "Payload": {"TunnelName": "website"}})
        )
        self.assertFalse(stop[0].active)

    def test_rejects_non_public_url(self) -> None:
        events = parse_log_line(
            log_line(
                {
                    "Type": "NewTunnel",
                    "Payload": {
                        "TunnelName": "bad",
                        "Protocol": "https",
                        "Url": "not a url",
                    },
                }
            )
        )
        self.assertEqual(events, [])


class DiscoveryTests(unittest.TestCase):
    def test_combines_config_and_incremental_log_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "cpolar.yml"
            log = base / "cpolar_service.log"
            config.write_text(
                "tunnels:\n  website:\n    proto: http\n    addr: 8080\n",
                encoding="utf-8",
            )
            log.write_text(
                log_line(
                    start_packet(
                        "website", [("https", "https://first.cpolar.top")]
                    )
                ),
                encoding="utf-8",
            )
            discovery = TunnelDiscovery(config, log)
            first = discovery.discover()[0]
            self.assertEqual(first.display_url, "https://first.cpolar.top")
            self.assertEqual(first.local_addr, "8080")

            with log.open("a", encoding="utf-8") as handle:
                handle.write(
                    log_line(
                        start_packet(
                            "website", [("https", "https://second.cpolar.top")]
                        ),
                        "2026-08-11T19:00:00+08:00",
                    )
                )
            second = discovery.discover()[0]
            self.assertEqual(second.display_url, "https://second.cpolar.top")


if __name__ == "__main__":
    unittest.main()
