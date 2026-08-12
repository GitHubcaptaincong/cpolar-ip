from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .config import load_cpolar_definitions
from .log_parser import apply_events, parse_log_line
from .models import TunnelInfo, TunnelRuntime


_DATED_LOG_RE = re.compile(r"^cpolar_service\.log\.\d{8}$")


def _file_identity(path: Path) -> Tuple[int, int, int]:
    stat = path.stat()
    return int(stat.st_dev), int(stat.st_ino), int(stat.st_size)


def _initial_log_candidates(log_path: Path, maximum: int = 4) -> List[Path]:
    candidates: List[Path] = []
    if log_path.exists():
        candidates.append(log_path)
    try:
        dated = [
            item
            for item in log_path.parent.iterdir()
            if item.is_file() and _DATED_LOG_RE.match(item.name)
        ]
    except OSError:
        dated = []
    dated.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    candidates.extend(dated[:maximum])

    unique: Dict[Tuple[int, int], Path] = {}
    for candidate in candidates:
        try:
            dev, inode, _ = _file_identity(candidate)
        except OSError:
            continue
        unique.setdefault((dev, inode), candidate)
    ordered = list(unique.values())
    ordered.sort(key=lambda item: item.stat().st_mtime)
    return ordered


class TunnelDiscovery:
    """Incrementally combines cpolar.yml definitions with public URLs from logs."""

    def __init__(self, config_path: Path, log_path: Path) -> None:
        self.config_path = config_path
        self.log_path = log_path
        self._states: Dict[str, TunnelRuntime] = {}
        self._current_identity: Tuple[int, int] | None = None
        self._current_offset = 0
        self._initialized = False
        self._lock = threading.RLock()

    @property
    def runtime_states(self) -> Dict[str, TunnelRuntime]:
        return self._states

    def _consume_file(self, path: Path, offset: int = 0) -> int:
        with path.open("rb") as handle:
            handle.seek(offset)
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace")
                apply_events(self._states, parse_log_line(line))
            return handle.tell()

    def _initialize_logs(self) -> None:
        for candidate in _initial_log_candidates(self.log_path):
            try:
                self._consume_file(candidate)
            except OSError:
                continue
        try:
            dev, inode, _ = _file_identity(self.log_path)
            self._current_identity = (dev, inode)
            self._current_offset = self.log_path.stat().st_size
        except OSError:
            self._current_identity = None
            self._current_offset = 0
        self._initialized = True

    def _consume_updates(self) -> None:
        try:
            dev, inode, size = _file_identity(self.log_path)
        except OSError:
            return
        identity = (dev, inode)
        if identity != self._current_identity or size < self._current_offset:
            offset = 0
        else:
            offset = self._current_offset
        try:
            self._current_offset = self._consume_file(self.log_path, offset)
            self._current_identity = identity
        except OSError:
            return

    def discover(self) -> List[TunnelInfo]:
        with self._lock:
            if not self._initialized:
                self._initialize_logs()
            else:
                self._consume_updates()

            definitions = load_cpolar_definitions(self.config_path)
            names = sorted(set(definitions) | set(self._states), key=str.casefold)
            result: List[TunnelInfo] = []
            for name in names:
                definition = definitions.get(name)
                runtime = self._states.get(name, TunnelRuntime())
                result.append(
                    TunnelInfo(
                        name=name,
                        proto=definition.proto if definition else "",
                        local_addr=definition.local_addr if definition else "",
                        region=definition.region if definition else "",
                        start_enabled=definition.start_enabled if definition else True,
                        active=runtime.active,
                        public_urls=dict(runtime.public_urls),
                        updated_at=runtime.updated_at,
                    )
                )
            return result
