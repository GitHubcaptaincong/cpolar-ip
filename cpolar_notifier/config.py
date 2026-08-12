from __future__ import annotations

import ast
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .models import TunnelDefinition


APP_NAME = "CpolarNotifier"


DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "version": 1,
    "cpolar_config_path": "",
    "cpolar_log_path": "",
    "selected_tunnels": [],
    "interval_seconds": 60,
    "notify_on_first_seen": True,
    "providers": {
        "serverchan": {"enabled": False},
        "smtp": {
            "enabled": False,
            "host": "smtp.qq.com",
            "port": 465,
            "security": "SSL",
            "username": "",
            "sender": "",
            "recipients": "",
        },
        "telegram": {"enabled": False, "chat_id": ""},
    },
}


def app_data_dir() -> Path:
    override = os.environ.get("CPOLAR_NOTIFIER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / APP_NAME


def default_cpolar_config_path() -> Path:
    override = os.environ.get("CPOLAR_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cpolar" / "cpolar.yml"


def default_cpolar_log_path(config_path: Path | None = None) -> Path:
    override = os.environ.get("CPOLAR_LOG")
    if override:
        return Path(override).expanduser()
    base = (config_path or default_cpolar_config_path()).parent
    return base / "logs" / "cpolar_service.log"


def _strip_yaml_comment(value: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif char == "#" and quote is None:
            return value[:index].rstrip()
    return value.strip()


def _parse_scalar(value: str) -> str:
    value = _strip_yaml_comment(value.strip())
    if not value:
        return ""
    if value[0:1] in ("'", '"') and value[-1:] == value[0]:
        try:
            parsed = ast.literal_eval(value)
            return str(parsed)
        except (SyntaxError, ValueError):
            return value[1:-1]
    return value


def parse_cpolar_config(text: str) -> Dict[str, TunnelDefinition]:
    """Parse the small tunnels section without loading the cpolar authtoken."""

    definitions: Dict[str, TunnelDefinition] = {}
    in_tunnels = False
    current: TunnelDefinition | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            in_tunnels = stripped == "tunnels:"
            current = None
            continue
        if not in_tunnels:
            continue

        tunnel_match = re.match(r"^ {2}([^:#][^:]*)\s*:\s*$", raw_line)
        if tunnel_match:
            name = tunnel_match.group(1).strip().strip("'\"")
            current = TunnelDefinition(name=name)
            definitions[name] = current
            continue

        field_match = re.match(r"^ {4,}([A-Za-z0-9_-]+)\s*:\s*(.*)$", raw_line)
        if current is None or not field_match:
            continue
        key, raw_value = field_match.groups()
        value = _parse_scalar(raw_value)
        if key == "proto":
            current.proto = value
        elif key == "addr":
            current.local_addr = value
        elif key == "region":
            current.region = value
        elif key == "start_type":
            current.start_enabled = value.lower() not in {"disable", "disabled", "false", "off", "0"}
        elif key == "id":
            current.tunnel_id = value

    return definitions


def load_cpolar_definitions(path: Path) -> Dict[str, TunnelDefinition]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return parse_cpolar_config(text)


def _merge_defaults(defaults: Mapping[str, Any], loaded: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(defaults))
    for key, value in loaded.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


class ConfigStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or app_data_dir()
        self.path = self.base_dir / "config.json"

    def load(self) -> Dict[str, Any]:
        loaded: Mapping[str, Any] = {}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, Mapping):
                    loaded = value
            except (OSError, ValueError):
                loaded = {}
        config = _merge_defaults(DEFAULT_APP_CONFIG, loaded)
        config["interval_seconds"] = max(15, min(3600, int(config["interval_seconds"])))
        return config

    def save(self, config: Mapping[str, Any]) -> None:
        normalized = _merge_defaults(DEFAULT_APP_CONFIG, config)
        normalized["interval_seconds"] = max(
            15, min(3600, int(normalized["interval_seconds"]))
        )
        atomic_write_json(self.path, normalized)


def provider_names(config: Mapping[str, Any], enabled_only: bool = True) -> Iterable[str]:
    providers = config.get("providers", {})
    if not isinstance(providers, Mapping):
        return []
    names = []
    for name, settings in providers.items():
        if not enabled_only or (isinstance(settings, Mapping) and settings.get("enabled")):
            names.append(str(name))
    return names
