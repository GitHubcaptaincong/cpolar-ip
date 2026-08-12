from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .models import TunnelRuntime


_LOG_MESSAGE_RE = re.compile(r"\bmsg=(\".*\")\s*$")
_LOG_TIME_RE = re.compile(r'^time="([^"]+)"')


@dataclass
class TunnelEvent:
    name: str
    active: Optional[bool]
    public_urls: Dict[str, str] = field(default_factory=dict)
    occurred_at: Optional[datetime] = None
    replace_urls: bool = False


def _parse_timestamp(line: str) -> Optional[datetime]:
    match = _LOG_TIME_RE.search(line)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1))
    except ValueError:
        return None


def _embedded_message(line: str) -> str:
    match = _LOG_MESSAGE_RE.search(line.rstrip("\r\n"))
    if not match:
        return ""
    try:
        value = json.loads(match.group(1))
        return value if isinstance(value, str) else ""
    except (TypeError, ValueError):
        return ""


def _embedded_packet(message: str) -> Mapping[str, Any] | None:
    start = message.find("{")
    if start < 0:
        return None
    try:
        packet = json.loads(message[start:])
    except (TypeError, ValueError):
        return None
    return packet if isinstance(packet, Mapping) else None


def _valid_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if re.match(r"^(?:https?|tcp|tls|ftp|data)://[^\s]+$", value, re.IGNORECASE):
        return value
    return ""


def parse_log_line(line: str) -> List[TunnelEvent]:
    message = _embedded_message(line)
    packet = _embedded_packet(message)
    if not packet:
        return []
    event_type = packet.get("Type")
    payload = packet.get("Payload")
    if not isinstance(payload, Mapping):
        return []
    occurred_at = _parse_timestamp(line)

    if event_type == "NewTunnel":
        name = str(payload.get("TunnelName") or "").strip()
        url = _valid_url(payload.get("Url"))
        protocol = str(payload.get("Protocol") or "").lower().strip()
        if name and url:
            if not protocol:
                protocol = url.split(":", 1)[0].lower()
            return [
                TunnelEvent(
                    name=name,
                    active=True,
                    public_urls={protocol: url},
                    occurred_at=occurred_at,
                )
            ]

    if event_type == "RespStartTunnel":
        grouped: Dict[str, Dict[str, str]] = {}
        items = payload.get("TunnelItems")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("TunnelName") or "").strip()
                url = _valid_url(item.get("Url"))
                protocol = str(item.get("Protocol") or "").lower().strip()
                if name and url:
                    if not protocol:
                        protocol = url.split(":", 1)[0].lower()
                    grouped.setdefault(name, {})[protocol] = url
        return [
            TunnelEvent(
                name=name,
                active=True,
                public_urls=urls,
                occurred_at=occurred_at,
                replace_urls=True,
            )
            for name, urls in grouped.items()
        ]

    if event_type in {"ReqStopTunnel", "RespStopTunnel"}:
        name = str(payload.get("TunnelName") or "").strip()
        if name:
            return [
                TunnelEvent(
                    name=name,
                    active=False,
                    occurred_at=occurred_at,
                )
            ]
    return []


def apply_events(
    states: Dict[str, TunnelRuntime], events: Iterable[TunnelEvent]
) -> Dict[str, TunnelRuntime]:
    for event in events:
        runtime = states.setdefault(event.name, TunnelRuntime())
        runtime.active = event.active
        if event.replace_urls:
            runtime.public_urls = dict(event.public_urls)
        else:
            runtime.public_urls.update(event.public_urls)
        if event.occurred_at is not None:
            runtime.updated_at = event.occurred_at
    return states
