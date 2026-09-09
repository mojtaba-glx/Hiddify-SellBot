"""
Shared/backup_integrity.py
==========================
Preflight validation helpers for backup ZIP archives before restore.

Stdlib-only (no Telegram, no bot-module imports) so it is safe to use from
any bot and from unit tests.

Goals:
- A corrupted or partial backup must be rejected BEFORE any real project
  file (database, JSON, receipts) is modified.
- Unsafe ZIP members (path traversal, absolute paths, duplicate names after
  normalization, symlinks, encryption, oversize members) are rejected.
- SQLite payloads are verified with PRAGMA quick_check over a read-only
  URI connection; the destination database is never touched here.
- Manifest entries (path/size/sha256) are validated against the bytes
  actually stored inside the ZIP, using chunk-based streaming so large
  members are never fully loaded into RAM.

Legacy compatibility:
- Backups without a manifest are accepted.
- Manifests without sha256 (older backups) are accepted.
- Extra ZIP members not listed in the manifest do NOT fail validation.
"""

import hashlib
import logging
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_MEMBERS",
    "DEFAULT_MAX_MEMBER_SIZE",
    "DEFAULT_MAX_TOTAL_SIZE",
    "COPY_CHUNK_SIZE",
    "sha256_bytes",
    "sha256_stream",
    "sha256_zip_member",
    "verify_sqlite_file",
    "normalize_member_name",
    "build_safe_zip_index",
    "verify_zip_crc",
    "copy_zip_member_to_file",
    "read_zip_member_bytes",
    "find_bot_manifest_member",
    "validate_backup_manifest",
]

# --- Safety limits (injectable in every function for testing) ---
DEFAULT_MAX_MEMBERS = 20000
DEFAULT_MAX_MEMBER_SIZE = 1024 ** 3          # 1 GiB per extracted member
DEFAULT_MAX_TOTAL_SIZE = 2 * 1024 ** 3       # 2 GiB total extracted
COPY_CHUNK_SIZE = 1024 * 1024                # 1 MiB streaming chunks

_ZIP_ENCRYPTED_FLAG = 0x1
_UNIX_MODE_SHIFT = 16
_S_IFMT = 0o170000
_S_IFLNK = 0o120000


# ---------------------------------------------------------------------------
# Hashing (chunk-based; never loads a whole member into RAM)
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(fp: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = fp.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _read_member_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_size: int):
    """Yield chunks of a ZIP member while enforcing the real byte count.

    The declared file_size is NOT trusted: the cap is applied to the number
    of bytes actually read from the stream (ZIP bomb protection).
    """
    read_total = 0
    with zf.open(info, "r") as src:
        while True:
            chunk = src.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            read_total += len(chunk)
            if read_total > max_size:
                raise ValueError(
                    f"حجم عضو «{info.filename}» از سقف مجاز ({max_size} بایت) بیشتر است."
                )
            yield chunk


def sha256_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, *, max_size: int = DEFAULT_MAX_MEMBER_SIZE) -> str:
    digest = hashlib.sha256()
    for chunk in _read_member_capped(zf, info, max_size):
        digest.update(chunk)
    return digest.hexdigest()


def read_zip_member_bytes(zf: zipfile.ZipFile, info: zipfile.ZipInfo, *, max_size: int = DEFAULT_MAX_MEMBER_SIZE) -> bytes:
    parts = []
    for chunk in _read_member_capped(zf, info, max_size):
        parts.append(chunk)
    return b"".join(parts)


def copy_zip_member_to_file(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dst_path: Path, *, max_size: int = DEFAULT_MAX_MEMBER_SIZE) -> int:
    """Copy a ZIP member to a local file in chunks, enforcing the real size cap."""
    written = 0
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with dst_path.open("wb") as dst:
        for chunk in _read_member_capped(zf, info, max_size):
            dst.write(chunk)
            written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# SQLite health check (read-only, no migration, no init_db, no writes)
# ---------------------------------------------------------------------------

def verify_sqlite_file(path: Path) -> None:
    """Raise ValueError unless `path` is a healthy SQLite database file.

    The file is opened through a read-only URI (mode=ro) and verified with
    PRAGMA quick_check. Only an exact ["ok"] result is accepted. The real
    destination database is never opened or modified by this function.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ValueError(f"فایل دیتابیس بکاپ پیدا نشد: {p.name}")
    try:
        size = p.stat().st_size
    except OSError as e:
        raise ValueError(f"دیتابیس بکاپ خوانده نشد ({p.name}): {e}") from e
    if size <= 0:
        raise ValueError(f"دیتابیس بکاپ خالی است: {p.name}")

    uri = f"file:{quote(str(p.resolve()))}?mode=ro"
    conn = None
    try:
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
        except sqlite3.Error as e:
            raise ValueError(f"بازکردن دیتابیس بکاپ ناموفق بود ({p.name}): {e}") from e
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as e:
            raise ValueError(f"فایل «{p.name}» یک دیتابیس SQLite معتبر نیست: {e}") from e
        results = [str(r[0]) for r in rows if r]
        if results != ["ok"]:
            preview = ", ".join(results[:3]) if results else "بدون نتیجه"
            raise ValueError(f"دیتابیس «{p.name}» سالم نیست (quick_check: {preview}). بازیابی متوقف شد.")
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


# ---------------------------------------------------------------------------
# Safe ZIP member index
# ---------------------------------------------------------------------------

def normalize_member_name(raw_name: str) -> str:
    """Normalize a ZIP member name; return "" when the name is unsafe.

    Unsafe means: empty after trimming, absolute path, Windows drive letter,
    or any ".." component after backslash-to-slash normalization.
    """
    name = str(raw_name or "").replace("\\", "/")
    if not name.strip():
        return ""
    lowered = name.lower()
    if lowered.startswith("/") or lowered.startswith("//"):
        return ""
    # Windows drive letter (C:/... or C:\...)
    if len(lowered) >= 2 and lowered[0].isalpha() and lowered[1] == ":":
        return ""
    name = name.lstrip("/")
    parts = [p for p in name.split("/") if p not in {"", "."}]
    if not parts:
        return ""
    if any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def build_safe_zip_index(
    zf: zipfile.ZipFile,
    *,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
) -> Dict[str, zipfile.ZipInfo]:
    """Build a {normalized_name: ZipInfo} index, rejecting unsafe archives.

    Rejected: empty/unsafe names, absolute paths, ".." traversal, duplicate
    names after normalization, symlinks, encrypted members, member count or
    size above the configured limits. Raises ValueError on any violation.
    """
    index: Dict[str, zipfile.ZipInfo] = {}
    total_declared = 0
    member_count = 0

    for info in zf.infolist():
        if info.is_dir():
            continue
        member_count += 1
        if member_count > max_members:
            raise ValueError(
                f"تعداد اعضای فایل zip ({member_count}) از سقف مجاز ({max_members}) بیشتر است."
            )

        normalized = normalize_member_name(info.filename)
        if not normalized:
            raise ValueError(
                f"فایل zip شامل عضو با نام ناامن است: «{info.filename}». بازیابی متوقف شد."
            )

        if normalized in index:
            raise ValueError(
                f"فایل zip شامل عضو تکراری بعد از نرمال‌سازی است: «{normalized}». بازیابی متوقف شد."
            )

        mode = (info.external_attr >> _UNIX_MODE_SHIFT) & _S_IFMT
        if mode == _S_IFLNK:
            raise ValueError(
                f"فایل zip شامل symlink است: «{info.filename}». بازیابی متوقف شد."
            )

        if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
            raise ValueError(
                f"عضو «{info.filename}» رمزنگاری‌شده است؛ zip رمزشده پشتیبانی نمی‌شود."
            )

        try:
            declared = int(info.file_size)
        except (TypeError, ValueError):
            raise ValueError(f"اندازه عضو «{info.filename}» نامعتبر است.") from None
        if declared < 0:
            raise ValueError(f"اندازه عضو «{info.filename}» نامعتبر است.")
        if declared > max_member_size:
            raise ValueError(
                f"حجم عضو «{info.filename}» ({declared} بایت) از سقف مجاز هر عضو "
                f"({max_member_size} بایت) بیشتر است."
            )
        total_declared += declared
        if total_declared > max_total_size:
            raise ValueError(
                f"مجموع حجم اعضای فایل zip از سقف مجاز ({max_total_size} بایت) بیشتر است."
            )

        index[normalized] = info

    return index


def verify_zip_crc(zf: zipfile.ZipFile) -> None:
    """Verify the CRC of every member using zipfile's standard testzip()."""
    bad_name = zf.testzip()
    if bad_name is not None:
        raise ValueError(
            f"فایل zip خراب است (خطای CRC در عضو «{bad_name}»). بازیابی متوقف شد."
        )


# ---------------------------------------------------------------------------
# Manifest handling
# ---------------------------------------------------------------------------

def find_bot_manifest_member(index: Dict[str, zipfile.ZipInfo]) -> str:
    """Locate the bot backup manifest (Backup_Bot_*.json) inside the index.

    Members under Receiptions/ or PanelBackups/ are ignored so a receipt or
    panel file that merely shares the name pattern is never mistaken for
    the manifest.
    """
    for name in sorted(index.keys()):
        if name.startswith("Receiptions/") or name.startswith("PanelBackups/"):
            continue
        base = name.rsplit("/", 1)[-1]
        if base.startswith("Backup_Bot_") and base.endswith(".json"):
            return name
    return ""


def validate_backup_manifest(
    manifest: Any,
    zf: zipfile.ZipFile,
    index: Dict[str, zipfile.ZipInfo],
    *,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
) -> Dict[str, int]:
    """Validate a backup manifest against the ZIP contents.

    - manifest without a "files" key is tolerated (legacy/full manifests).
    - files_count must match len(files) when present.
    - every listed path must exist inside the ZIP.
    - size (when present) must match the member's real size.
    - sha256 (when present) must match the bytes stored inside the ZIP;
      mismatch aborts the restore.
    - extra ZIP members not listed in the manifest are ignored.

    Returns {"entries": n, "hashed": m}. Raises ValueError on violations.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Manifest بکاپ نامعتبر است (آبجکت JSON نیست).")

    files = manifest.get("files")
    if files is None:
        return {"entries": 0, "hashed": 0}
    if not isinstance(files, list):
        raise ValueError("Manifest بکاپ نامعتبر است (فهرست files نامعتبر).")

    declared_count = manifest.get("files_count")
    if declared_count is not None:
        try:
            declared_count = int(declared_count)
        except (TypeError, ValueError):
            raise ValueError("Manifest بکاپ نامعتبر است (files_count عددی نیست).") from None
        if declared_count != len(files):
            raise ValueError(
                f"Manifest بکاپ ناسازگار است: files_count={declared_count} ولی "
                f"{len(files)} فایل فهرست شده است. بازیابی متوقف شد."
            )

    hashed = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("Manifest بکاپ نامعتبر است (یک فایل ثبت‌شده آبجکت نیست).")
        path = str(entry.get("path") or "").strip()
        if not path:
            raise ValueError("Manifest بکاپ نامعتبر است (مسیر خالی در فهرست files).")
        if path not in index:
            raise ValueError(
                f"فایل «{path}» در manifest ثبت شده ولی در بکاپ وجود ندارد. بازیابی متوقف شد."
            )
        info = index[path]

        if entry.get("size") is not None:
            try:
                declared_size = int(entry["size"])
            except (TypeError, ValueError):
                raise ValueError(f"Manifest بکاپ نامعتبر است (اندازه غیرعددی برای «{path}»).") from None
            if declared_size != int(info.file_size):
                raise ValueError(
                    f"اندازه فایل «{path}» با manifest نمی‌خواند "
                    f"({declared_size} ثبت‌شده در مقابل {int(info.file_size)} واقعی). بازیابی متوقف شد."
                )

        sha = str(entry.get("sha256") or "").strip().lower()
        if sha:
            actual = sha256_zip_member(zf, info, max_size=max_member_size)
            if actual != sha:
                raise ValueError(
                    f"هش SHA-256 فایل «{path}» با manifest نمی‌خواند؛ بکاپ خراب یا دستکاری‌شده است. "
                    "بازیابی متوقف شد."
                )
            hashed += 1

    return {"entries": len(files), "hashed": hashed}
