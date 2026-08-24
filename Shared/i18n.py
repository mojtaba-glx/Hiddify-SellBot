"""
Shared/i18n.py
==============
سبک چندزبانه برای Hiddify-SellBot (بدون وابستگی سنگین).

- فایل‌ها: locales/{fa,en,ru}.json  (یا Shared/locales/ برای سازگاری)
- کلیدها: "welcome", "buy_button", "status_title" ...
- استفاده: t("welcome", lang, full_name="Ali")  -> "سلام Ali عزیز"
- فال‌بک: اگر کلید در lang نبود، fa برمی‌گردد؛ اگر در fa هم نبود، خود کلید.

ذخیره زبان کاربر: userbot_users.language, agent_users.language (TEXT DEFAULT 'fa')
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

# ریشه پروژه
ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_DIRS = [
    ROOT / "locales",
    ROOT / "Shared" / "locales",
    Path(__file__).with_name("locales"),
]

_CACHE: Dict[str, Dict[str, str]] = {}
_DEFAULT_LANG = "fa"
_SUPPORTED = {"fa", "en", "ru"}


def _find_locales_dir() -> Path:
    for d in CANDIDATE_DIRS:
        if d.exists() and d.is_dir():
            return d
    # fallback: locales در روت
    return ROOT / "locales"


def _load_lang(lang: str) -> Dict[str, str]:
    lang = (lang or _DEFAULT_LANG).strip().lower()
    if lang not in _SUPPORTED:
        lang = _DEFAULT_LANG
    if lang in _CACHE:
        return _CACHE[lang]
    base = _find_locales_dir()
    path = base / f"{lang}.json"
    fallback = base / f"{_DEFAULT_LANG}.json"
    data: Dict[str, str] = {}
    # Try requested lang
    for p in (path, fallback):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    # Normalize to str
    norm: Dict[str, str] = {}
    for k, v in (data or {}).items():
        norm[str(k)] = str(v)
    _CACHE[lang] = norm
    return norm


def t(key: str, lang: str = "fa", **kwargs: Any) -> str:
    """ترجمه کلید. اگر kwargs داد، format می‌کند."""
    k = str(key or "").strip()
    if not k:
        return ""
    lg = (lang or _DEFAULT_LANG).strip().lower()
    if lg not in _SUPPORTED:
        lg = _DEFAULT_LANG
    # Try lang, then fa, then key itself
    for cand in (lg, _DEFAULT_LANG):
        d = _load_lang(cand)
        if k in d:
            txt = d[k]
            if kwargs:
                try:
                    return txt.format(**kwargs)
                except Exception:
                    return txt
            return txt
    # Fallback: return key with format attempt
    if kwargs:
        try:
            return k.format(**kwargs)
        except Exception:
            return k
    return k


def get_user_lang(telegram_id: int, default: str = "fa") -> str:
    """زبان ذخیره‌شده کاربر از DB، وگرنه fa."""
    try:
        from Shared import userbot_db
        u = userbot_db.get_user_by_telegram_id(int(telegram_id or 0))
        if u:
            lg = str(u.get("language") or "").strip().lower()
            if lg in _SUPPORTED:
                return lg
    except Exception:
        pass
    return default


def get_agent_lang(agent_id: int, default: str = "fa") -> str:
    try:
        from Shared import agent_db
        a = agent_db.get_agent_by_id(int(agent_id or 0))
        if a:
            lg = str((a or {}).get("language") or "").strip().lower()
            if lg in _SUPPORTED:
                return lg
    except Exception:
        pass
    return default


def clear_cache() -> None:
    _CACHE.clear()
