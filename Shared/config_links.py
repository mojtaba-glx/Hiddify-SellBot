from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse


VISIBLE_CONFIG_SCHEMES = (
    "vless",
    "vmess",
    "ss",
    "ssr",
    "tuic",
    "hysteria",
    "hysteria2",
    "hy2",
    "anytls",
    "wireguard",
    "wg",
    "socks",
    "ssh",
    "naive",
    "naive+https",
    "naive+quic",
    "mieru",
    "mierus",
    "snell",
    "tg",
    "mtproxies",
)

HIDDEN_CONFIG_PROTOCOLS = frozenset({"trojan"})

_SCHEME_BY_ALIAS = {
    "hy2": "hysteria2",
    "wg": "wireguard",
    "mierus": "mieru",
    "naive+https": "naive",
    "naive+quic": "naive",
    "mtproxies": "telegram",
    "tg": "telegram",
}

_PROTOCOL_ALIASES = {
    "shadowsocks": "ss",
    "hy2": "hysteria2",
    "hysteria-2": "hysteria2",
    "wg": "wireguard",
    "mierus": "mieru",
    "naive+https": "naive",
    "naive+quic": "naive",
    "naivehttps": "naive",
    "naivequic": "naive",
    "tg": "telegram",
}

_SCHEME_RE = re.compile(r"(?i)^([a-z][a-z0-9.+\-]{1,24})://")
_HIDDEN_CHARS = ("\ufeff", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c")


def clean_config_text(value: object) -> str:
    text = str(value or "").strip()
    for ch in _HIDDEN_CHARS:
        text = text.replace(ch, "")
    return text.strip()


def config_scheme(value: object) -> str:
    match = _SCHEME_RE.match(clean_config_text(value))
    if not match:
        return ""
    return str(match.group(1) or "").lower()


def normalize_protocol(value: object) -> str:
    raw = clean_config_text(value).lower().replace("_", "-")
    if not raw:
        return ""
    return _PROTOCOL_ALIASES.get(raw, raw)


def _is_telegram_proxy(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme.lower() == "mtproxies":
        return bool(parsed.netloc or parsed.path or parsed.query)
    if parsed.netloc.lower() not in {"proxy", "webproxy"}:
        return False
    return bool(parse_qs(parsed.query or "").get("server"))


def config_protocol(value: object) -> str:
    text = clean_config_text(value)
    scheme = config_scheme(text)
    if not scheme:
        return ""
    if scheme == "tg" and not _is_telegram_proxy(text):
        return ""
    return _SCHEME_BY_ALIAS.get(scheme, scheme)


def is_visible_config_link(value: object) -> bool:
    text = clean_config_text(value)
    scheme = config_scheme(text)
    if not scheme or scheme not in VISIBLE_CONFIG_SCHEMES:
        return False
    protocol = config_protocol(text)
    if not protocol or protocol in HIDDEN_CONFIG_PROTOCOLS:
        return False
    return True


def extract_visible_config_link(value: object) -> str:
    raw = clean_config_text(value).rstrip("'\",;)]}")
    if "://" not in raw or re.search(r"\s", raw) or len(raw) < 16:
        return ""
    if not is_visible_config_link(raw):
        return ""
    return raw


def filter_visible_config_links(links: Iterable[object], protocol: str = "") -> list[str]:
    target = normalize_protocol(protocol)
    out: list[str] = []
    seen: set[str] = set()
    for item in links:
        link = clean_config_text(item)
        if not link or link in seen or not is_visible_config_link(link):
            continue
        if target and config_protocol(link) != target:
            continue
        seen.add(link)
        out.append(link)
    return out
