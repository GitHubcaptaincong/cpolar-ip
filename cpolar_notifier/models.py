from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class TunnelDefinition:
    name: str
    proto: str = ""
    local_addr: str = ""
    region: str = ""
    start_enabled: bool = True
    tunnel_id: str = ""


@dataclass
class TunnelRuntime:
    active: Optional[bool] = None
    public_urls: Dict[str, str] = field(default_factory=dict)
    updated_at: Optional[datetime] = None


@dataclass
class TunnelInfo:
    name: str
    proto: str = ""
    local_addr: str = ""
    region: str = ""
    start_enabled: bool = True
    active: Optional[bool] = None
    public_urls: Dict[str, str] = field(default_factory=dict)
    updated_at: Optional[datetime] = None

    @property
    def display_url(self) -> str:
        for protocol in ("https", "tcp", "http", "tls", "ftp"):
            if self.public_urls.get(protocol):
                return self.public_urls[protocol]
        return next(iter(self.public_urls.values()), "")

    @property
    def all_urls(self) -> List[str]:
        return [self.public_urls[key] for key in sorted(self.public_urls)]

    @property
    def fingerprint(self) -> str:
        return "\n".join(
            f"{protocol}={self.public_urls[protocol]}"
            for protocol in sorted(self.public_urls)
        )

    @property
    def status_text(self) -> str:
        if self.active is True:
            return "在线"
        if self.active is False:
            return "已停止"
        if self.public_urls:
            return "地址已发现"
        return "等待地址"
