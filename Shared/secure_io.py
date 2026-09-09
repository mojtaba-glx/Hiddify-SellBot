"""
Shared/secure_io.py
===================
Stdlib-only security utilities shared by all bots:

- atomic, locked, permission-preserving .env updates
- private-file permission enforcement (0600)
- secret redaction for logs

Nothing here imports Telegram or project modules, and no secret value is
ever included in an exception message or log record produced here.
"""

import fcntl
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Union

logger = logging.getLogger(__name__)

__all__ = [
    "ENV_KEY_RE",
    "validate_env_key",
    "validate_env_value",
    "atomic_update_env",
    "ensure_private_file",
    "ensure_private_tree",
    "redact_sensitive_text",
    "safe_exception_name",
]

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# chmod permission bits
_FILE_MODE = 0o600
_DIR_MODE = 0o700

# Generic secret shapes that must never survive into a log line.
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/-]+=*)")
_BEARER_WORD_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/-]{8,})")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)([^\s,;&\"]+(?:\s+[^\s,;&\"]+)?[\"']?)"
)
_SECRET_KV_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key|credential|webhook[_-]?secret)[A-Za-z0-9_]*[\"']?"
    r"\s*[=:]\s*)([^\s,;&\"']{6,})"
)
# Telegram API URLs contain the token immediately after the literal ``bot``
# (``.../bot123456:secret/getMe``).  A leading ``\b`` therefore misses the
# token because both ``t`` and the first digit are word characters.  Digit and
# token-alphabet lookarounds cover both standalone tokens and URL tokens,
# including secrets whose last character is ``-`` or ``_``.
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?<!\d)(\d{6,15}:[A-Za-z0-9_-]{25,})(?![A-Za-z0-9_-])"
)
_WEBHOOK_SECRET_RE = re.compile(
    r"(?i)(webhook[_-]?secret[\"']?\s*[=:]\s*[\"']?)([^\s,;&\"]{6,}[\"']?)"
)


def validate_env_key(key: str) -> bool:
    """True only for shell-safe env keys: ^[A-Za-z_][A-Za-z0-9_]*$"""
    return bool(ENV_KEY_RE.match(str(key or "")))


def validate_env_value(value: str) -> bool:
    """Reject values that would break the single-line env file format
    (newline, carriage return, NUL)."""
    v = str(value if value is not None else "")
    if "\n" in v or "\r" in v or "\x00" in v:
        return False
    return True


def redact_sensitive_text(value: str) -> str:
    """Return `value` with any recognized secret material replaced by a
    fixed placeholder. Safe to call on arbitrary text; never raises."""
    text = str(value if value is not None else "")
    if not text:
        return text
    try:
        text = _TELEGRAM_TOKEN_RE.sub("<redacted-token>", text)
        text = _BEARER_RE.sub(r"\1<redacted>", text)
        text = _BEARER_WORD_RE.sub(r"\1<redacted>", text)
        text = _AUTH_HEADER_RE.sub(r"\1<redacted>", text)
        text = _WEBHOOK_SECRET_RE.sub(r"\1<redacted>", text)
        text = _SECRET_KV_RE.sub(r"\1<redacted>", text)
        return text
    except Exception:
        # Redaction must never break the caller; drop the whole text rather
        # than risk leaking the input.
        return "<redacted-text>"


def safe_exception_name(exception: BaseException) -> str:
    """The exception class name only — no str(exception), which may embed
    secrets such as tokens."""
    return type(exception).__name__ if exception is not None else "UnknownError"


def ensure_private_file(path: Union[str, Path]) -> bool:
    """Tighten a file's permissions to 0600. Missing files are ignored."""
    p = Path(path)
    try:
        if not p.exists() or not p.is_file():
            return False
        current = p.stat().st_mode & 0o777
        if current != _FILE_MODE:
            os.chmod(p, _FILE_MODE)
        return True
    except OSError as e:
        logger.warning(
            "secure_io: cannot tighten permissions on %s: %s: %s",
            p.name, safe_exception_name(e), e,
        )
        return False


def ensure_private_tree(directory: Union[str, Path], *, files: bool = True) -> None:
    """chmod a directory to 0700 and (optionally) its direct files to 0600.
    Missing paths are silently skipped; failures are logged by name only."""
    d = Path(directory)
    try:
        if d.is_dir():
            current = d.stat().st_mode & 0o777
            if current != _DIR_MODE:
                os.chmod(d, _DIR_MODE)
            if files:
                for child in d.iterdir():
                    if child.is_file():
                        ensure_private_file(child)
    except OSError as e:
        logger.warning(
            "secure_io: cannot tighten permissions on directory %s: %s: %s",
            d.name, safe_exception_name(e), e,
        )


def _fsync_dir(path: Path) -> None:
    """Open the directory descriptor and fsync it for durability after a
    rename. Never changes permissions; failures are logged by name only."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError as e:
        logger.warning(
            "secure_io: cannot open directory for fsync: %s: %s",
            path.name, safe_exception_name(e),
        )
        return
    try:
        os.fsync(fd)
    except OSError as e:
        logger.warning(
            "secure_io: directory fsync failed: %s: %s",
            path.name, safe_exception_name(e),
        )
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _key_of(line: str) -> str:
    """Return the env key of a KEY=VALUE line, or "" for non-assignment
    lines (comments, blanks, lines without '=')."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    return stripped.split("=", 1)[0].strip()


def atomic_update_env(path: Union[str, Path], updates: Dict[str, str]) -> None:
    """Atomically update KEY=VALUE pairs in an env file.

    Guarantees:
    - keys must match ^[A-Za-z_][A-Za-z0-9_]*$
    - values must not contain newline, carriage return or NUL
    - comments, blank lines, line ordering, unrelated keys and the exact
      trailing-newline state of the file are preserved
    - for an existing key only its assignment is rewritten; duplicates of
      the same key collapse to a single occurrence
    - a shared sidecar lock (.env.lock, fcntl.flock) serializes writers;
      the lock file itself is chmod 0600 immediately after creation
    - the temp file lives in the same directory, chmod 0600, fsynced,
      then installed with os.replace; the final file is chmod 0600
    - after the replace, the parent directory is only opened and fsynced
      for durability — its permissions are never modified
    - on any error the original file is left untouched and the temp file
      is removed
    - no secret value is included in exceptions or log records

    Raises ValueError on invalid input; OSError/I/O errors propagate after
    cleanup.
    """
    env_path = Path(path)
    clean_updates: Dict[str, str] = {}
    for key, value in dict(updates or {}).items():
        key = str(key or "").strip()
        if not validate_env_key(key):
            raise ValueError(
                f"atomic_update_env: rejected invalid env key (does not match "
                f"{ENV_KEY_RE.pattern}): key name withheld"
            )
        if not validate_env_value(value):
            raise ValueError(
                "atomic_update_env: rejected value for a key because it "
                "contains newline/CR/NUL (key name withheld)"
            )
        clean_updates[key] = str(value)
    if not clean_updates:
        return

    # Create the parent directory only when missing; an existing directory
    # (e.g. the project root) keeps its current permissions untouched.
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = env_path.with_name(env_path.name + ".lock")

    # Serialize concurrent writers across processes with flock on a sidecar
    # lock file (never on .env itself, so replace is always safe).
    with open(lock_path, "a+") as lock_f:
        try:
            os.fchmod(lock_f.fileno(), _FILE_MODE)
        except OSError as e:
            logger.warning(
                "secure_io: cannot chmod lock file: %s: %s",
                lock_path.name, safe_exception_name(e),
            )
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            if env_path.exists():
                raw = env_path.read_text(encoding="utf-8")
            else:
                raw = ""
            # keepends preserves blank lines and the trailing-newline state.
            lines = raw.splitlines(keepends=True)

            final_lines = []
            seen = set()
            replaced = set()
            for line in lines:
                body = line.rstrip("\r\n")
                key = _key_of(body)
                if key in clean_updates:
                    if key in seen:
                        # duplicate of an already-handled key: drop it
                        continue
                    eol = line[len(body):]
                    final_lines.append(f"{key}={clean_updates[key]}{eol}")
                    seen.add(key)
                    replaced.add(key)
                    continue
                final_lines.append(line)

            # Append still-missing keys, matching the file's ending style so
            # an existing file never loses its final newline and a new file
            # stays well-formed.
            missing = [k for k in clean_updates if k not in replaced]
            if missing:
                file_endsWith_newline = (not lines) or lines[-1].endswith(("\n", "\r"))
                if lines and not file_endsWith_newline:
                    final_lines.append("\n")
                for k in missing:
                    final_lines.append(f"{k}={clean_updates[k]}\n")

            new_content = "".join(final_lines)

            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{env_path.name}.", suffix=".tmp", dir=str(env_path.parent)
            )
            tmp_path = Path(tmp_name)
            try:
                os.fchmod(fd, _FILE_MODE)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(new_content)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, env_path)
            except BaseException:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "secure_io: failed to clean temp file for %s", env_path.name
                    )
                raise
            # The final file must always be 0600 (replace kept the temp mode,
            # but enforce defensively in case of platform quirks).
            ensure_private_file(env_path)
            # Durability only: open + fsync the directory (no chmod).
            _fsync_dir(env_path.parent)
        finally:
            try:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
