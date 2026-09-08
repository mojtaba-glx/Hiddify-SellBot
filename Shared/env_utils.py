"""
Shared/env_utils.py
====================
Safe numeric environment variable parsing.

Direct int(...)/float(...) conversions of .env values at import/startup time
can crash the whole bot when an operator mistypes a value (e.g. ADMIN_ID=abc).
These helpers never raise and always fall back to the documented default.

- Stdlib only; no Telegram or project-internal dependencies.
- Never mutates os.environ.
- Warnings log the variable name only, never its raw value (which may be
  considered sensitive).
"""

import logging
import math
import os

logger = logging.getLogger(__name__)

__all__ = ["env_int", "env_float"]


def _raw(name: str) -> str:
    """Return the raw env value as a string; missing or blank values -> ""."""
    value = os.getenv(name)
    if value is None:
        return ""
    return value.strip()


def _log_invalid(name: str, kind: str) -> None:
    logger.warning(
        "env_utils: invalid numeric value for %s (expected %s); using default",
        name,
        kind,
    )


def _clamp(value, minimum, maximum):
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def env_int(name: str, default: int, *, minimum: int = None, maximum: int = None) -> int:
    """Read an integer from the environment; fall back to `default` on any
    parse failure. Values outside [minimum, maximum] are clamped (the default
    is clamped too)."""
    raw = _raw(name)
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            _log_invalid(name, "integer")
            value = default
    if minimum is not None or maximum is not None:
        value = _clamp(value, minimum, maximum)
    return value


def env_float(name: str, default: float, *, minimum: float = None, maximum: float = None) -> float:
    """Read a float from the environment; fall back to `default` on any parse
    failure. NaN and +/-Infinity are treated as invalid. Values outside
    [minimum, maximum] are clamped (the default is clamped too)."""
    raw = _raw(name)
    if not raw:
        value = default
    else:
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            _log_invalid(name, "number")
            parsed = None
        if parsed is None or math.isnan(parsed) or math.isinf(parsed):
            if parsed is not None:
                _log_invalid(name, "finite number")
            value = default
        else:
            value = parsed
    if minimum is not None or maximum is not None:
        value = _clamp(value, minimum, maximum)
    return value
