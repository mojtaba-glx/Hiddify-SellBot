from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


def _bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class XNetConfig:
    enabled: bool = False
    base_url: str = ""
    token: str = ""
    timeout: float = 10.0
    verify_tls: bool = True

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "XNetConfig":
        e = env or os.environ
        return cls(
            enabled=_bool(e.get("XNET_ENABLED", "0"), False),
            base_url=e.get("XNET_BASE_URL", "").strip(),
            token=e.get("XNET_API_TOKEN", "").strip(),
            timeout=float(e.get("XNET_API_TIMEOUT", "10")),
            verify_tls=_bool(e.get("XNET_VERIFY_TLS", "1"), True),
        )

    def validate(self) -> None:
        if self.enabled and (not self.base_url or not self.token):
            raise ValueError("XNET_BASE_URL and XNET_API_TOKEN are required when XNET_ENABLED=1")
