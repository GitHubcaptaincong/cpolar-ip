from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping

from .config import app_data_dir, atomic_write_json, provider_names
from .discovery import TunnelDiscovery
from .models import TunnelInfo
from .notifications import NotificationResult, build_provider
from .secrets import SecretStore


@dataclass
class AddressChange:
    tunnel: TunnelInfo
    old_urls: List[str] = field(default_factory=list)
    first_seen: bool = False


@dataclass
class CheckReport:
    checked_at: datetime
    tunnels: List[TunnelInfo]
    changes: List[AddressChange] = field(default_factory=list)
    notifications: List[NotificationResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class StateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or app_data_dir()
        self.path = self.base_dir / "state.json"
        self._lock = threading.RLock()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {"version": 1, "tunnels": {}}
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    value.setdefault("version", 1)
                    value.setdefault("tunnels", {})
                    return value
            except (OSError, ValueError):
                pass
            return {"version": 1, "tunnels": {}}

    def save(self, state: Mapping[str, Any]) -> None:
        with self._lock:
            atomic_write_json(self.path, state)


def _message_for_changes(changes: List[AddressChange], checked_at: datetime) -> tuple[str, str]:
    all_first = all(change.first_seen for change in changes)
    title = "cpolar 当前公网地址" if all_first else "cpolar 公网地址已变化"
    lines = [f"检测时间：{checked_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}"]
    for change in changes:
        tunnel = change.tunnel
        lines.extend(["", f"隧道：{tunnel.name} ({tunnel.proto or '未知协议'})"])
        if change.old_urls:
            lines.append("原地址：")
            lines.extend(f"  {url}" for url in change.old_urls)
        lines.append("当前地址：")
        lines.extend(f"  {url}" for url in tunnel.all_urls)
    lines.extend(["", "本消息由本机 cpolar 地址监控工具自动发送。"])
    return title, "\n".join(lines)


class MonitorEngine:
    def __init__(
        self,
        discovery: TunnelDiscovery,
        secrets: SecretStore,
        state_store: StateStore,
    ) -> None:
        self.discovery = discovery
        self.secrets = secrets
        self.state_store = state_store
        self._check_lock = threading.Lock()
        self._retry_after: Dict[str, float] = {}

    def check_once(self, config: Mapping[str, Any]) -> CheckReport:
        if not self._check_lock.acquire(blocking=False):
            return CheckReport(
                checked_at=datetime.now().astimezone(),
                tunnels=[],
                errors=["已有一次检查正在进行"],
            )
        try:
            return self._check_once_locked(config)
        finally:
            self._check_lock.release()

    def _check_once_locked(self, config: Mapping[str, Any]) -> CheckReport:
        checked_at = datetime.now().astimezone()
        try:
            tunnels = self.discovery.discover()
        except (OSError, ValueError) as exc:
            return CheckReport(checked_at=checked_at, tunnels=[], errors=[str(exc)])

        report = CheckReport(checked_at=checked_at, tunnels=tunnels)
        selected = {str(name) for name in config.get("selected_tunnels", [])}
        if not selected:
            return report

        state = self.state_store.load()
        tunnel_state = state.setdefault("tunnels", {})
        if not isinstance(tunnel_state, MutableMapping):
            tunnel_state = {}
            state["tunnels"] = tunnel_state

        changes_by_provider: Dict[str, List[AddressChange]] = {
            name: [] for name in provider_names(config, enabled_only=True)
        }
        discovered_changes: Dict[str, AddressChange] = {}

        for tunnel in tunnels:
            if tunnel.name not in selected or not tunnel.fingerprint or tunnel.active is False:
                continue
            saved = tunnel_state.setdefault(tunnel.name, {})
            if not isinstance(saved, MutableMapping):
                saved = {}
                tunnel_state[tunnel.name] = saved
            old_fingerprint = str(saved.get("observed_fingerprint") or "")
            old_urls = [str(item) for item in saved.get("observed_urls", []) if item]
            first_seen = not old_fingerprint
            changed = old_fingerprint != tunnel.fingerprint
            saved["observed_fingerprint"] = tunnel.fingerprint
            saved["observed_urls"] = tunnel.all_urls
            saved["last_seen_at"] = checked_at.isoformat()
            notified = saved.setdefault("notified", {})
            if not isinstance(notified, MutableMapping):
                notified = {}
                saved["notified"] = notified

            if changed:
                change = AddressChange(tunnel=tunnel, old_urls=old_urls, first_seen=first_seen)
                discovered_changes[tunnel.name] = change

            if first_seen and not bool(config.get("notify_on_first_seen", True)):
                for provider_name in changes_by_provider:
                    notified[provider_name] = tunnel.fingerprint
                continue

            for provider_name in changes_by_provider:
                if str(notified.get(provider_name) or "") != tunnel.fingerprint:
                    provider_first_seen = not bool(notified.get(provider_name))
                    change = discovered_changes.get(tunnel.name)
                    if change is None or provider_first_seen:
                        change = AddressChange(
                            tunnel=tunnel,
                            old_urls=[] if provider_first_seen else old_urls,
                            first_seen=provider_first_seen,
                        )
                    changes_by_provider[provider_name].append(change)

        report.changes = list(discovered_changes.values())

        now_monotonic = time.monotonic()
        for provider_name, changes in changes_by_provider.items():
            if not changes:
                continue
            if self._retry_after.get(provider_name, 0) > now_monotonic:
                continue
            try:
                provider = build_provider(provider_name, config, self.secrets)
            except (OSError, ValueError, RuntimeError) as exc:
                report.notifications.append(
                    NotificationResult(provider_name, False, str(exc)[:300])
                )
                self._retry_after[provider_name] = now_monotonic + 300
                continue
            title, body = _message_for_changes(changes, checked_at)
            result = provider.send(title, body)
            report.notifications.append(result)
            if result.success:
                self._retry_after.pop(provider_name, None)
                for change in changes:
                    saved = tunnel_state[change.tunnel.name]
                    saved["notified"][provider_name] = change.tunnel.fingerprint
                    saved["last_notified_at"] = checked_at.isoformat()
            else:
                self._retry_after[provider_name] = now_monotonic + 300

        self.state_store.save(state)
        return report
