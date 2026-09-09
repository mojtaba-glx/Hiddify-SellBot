import hashlib
import io
import json
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from Shared import backup_integrity as bi


def _make_sqlite(path: Path, table: str = "t") -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute(f"INSERT INTO {table} (value) VALUES ('hello')")
    conn.commit()
    conn.close()
    return path


class ZipBuilder:
    """Helper to build in-memory or on-disk ZIPs for tests."""

    def __init__(self):
        self.buf = io.BytesIO()

    def add_bytes(self, name: str, data: bytes) -> "ZipBuilder":
        with zipfile.ZipFile(self.buf, mode="a") as zf:
            zf.writestr(name, data)
        return self

    def add_file(self, name: str, src: Path) -> "ZipBuilder":
        with zipfile.ZipFile(self.buf, mode="a") as zf:
            zf.write(src, arcname=name)
        return self

    def add_symlink(self, name: str, target: str = "/etc/passwd") -> "ZipBuilder":
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.buf, mode="a") as zf:
            zf.writestr(info, target)
        return self

    def add_encrypted_flag(self, name: str, data: bytes) -> "ZipBuilder":
        """Mark a member as encrypted by setting the GP flag bit in the raw
        central directory record (zipfile.writestr clears the flag itself)."""
        with zipfile.ZipFile(self.buf, mode="a") as zf:
            zf.writestr(name, data)
        raw = bytearray(self.buf.getvalue())
        sig = b"PK\x01\x02"
        pos = 0
        while True:
            pos = raw.find(sig, pos)
            if pos < 0:
                break
            name_len = int.from_bytes(raw[pos + 28:pos + 30], "little")
            entry_name = bytes(raw[pos + 46:pos + 46 + name_len]).decode("utf-8", "replace")
            if entry_name == name:
                raw[pos + 8] |= 0x01  # general purpose bit flag: encrypted
                break
            pos += 4
        self.buf = io.BytesIO(bytes(raw))
        return self

    def corrupt_last_member_crc(self) -> None:
        data = bytearray(self.buf.getvalue())
        # Flip bytes near the end (inside the last member's data area).
        for i in range(len(data) - 64, len(data) - 30):
            data[i] ^= 0xFF
        self.buf = io.BytesIO(bytes(data))

    def open(self) -> zipfile.ZipFile:
        self.buf.seek(0)
        return zipfile.ZipFile(self.buf, mode="r")


class Sha256Tests(unittest.TestCase):
    def test_sha256_bytes_matches_hashlib(self):
        data = b"hello world"
        self.assertEqual(bi.sha256_bytes(data), hashlib.sha256(data).hexdigest())

    def test_sha256_stream_chunked(self):
        data = (b"x" * (bi.COPY_CHUNK_SIZE * 2 + 123))
        self.assertEqual(bi.sha256_stream(io.BytesIO(data)), hashlib.sha256(data).hexdigest())

    def test_sha256_zip_member(self):
        data = b"A" * 5000
        zb = ZipBuilder().add_bytes("f.bin", data)
        with zb.open() as zf:
            info = zf.infolist()[0]
            self.assertEqual(bi.sha256_zip_member(zf, info), hashlib.sha256(data).hexdigest())


class SqliteValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_healthy_sqlite_accepted(self):
        p = _make_sqlite(self.dir / "ok.db")
        bi.verify_sqlite_file(p)  # must not raise

    def test_missing_file_rejected(self):
        with self.assertRaises(ValueError):
            bi.verify_sqlite_file(self.dir / "nope.db")

    def test_empty_file_rejected(self):
        p = self.dir / "empty.db"
        p.write_bytes(b"")
        with self.assertRaises(ValueError):
            bi.verify_sqlite_file(p)

    def test_non_sqlite_file_rejected(self):
        p = self.dir / "text.db"
        p.write_text("this is definitely not a database")
        with self.assertRaises(ValueError):
            bi.verify_sqlite_file(p)

    def test_truncated_sqlite_rejected(self):
        p = _make_sqlite(self.dir / "trunc.db")
        raw = p.read_bytes()
        p.write_bytes(raw[: len(raw) // 3])
        with self.assertRaises(ValueError):
            bi.verify_sqlite_file(p)

    def test_corrupted_page_rejected(self):
        p = _make_sqlite(self.dir / "corrupt.db", table="big")
        conn = sqlite3.connect(str(p))
        conn.executemany(
            "INSERT INTO big (value) VALUES (?)",
            [(f"v{i}" * 50,) for i in range(500)],
        )
        conn.commit()
        conn.close()
        raw = bytearray(p.read_bytes())
        for i in range(4096, min(len(raw), 8192)):
            raw[i] ^= 0xFF
        p.write_bytes(bytes(raw))
        with self.assertRaises(ValueError):
            bi.verify_sqlite_file(p)

    def test_does_not_mutate_file(self):
        p = _make_sqlite(self.dir / "ro.db")
        before = p.read_bytes()
        bi.verify_sqlite_file(p)
        self.assertEqual(p.read_bytes(), before)


class ZipIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_healthy_zip_index(self):
        zb = ZipBuilder().add_bytes("Shared/servers.json", b"{}").add_bytes("Shared/plans.json", b"{}")
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            self.assertEqual(set(index.keys()), {"Shared/servers.json", "Shared/plans.json"})

    def test_backslash_path_normalized(self):
        zb = ZipBuilder().add_bytes("Shared\\servers.json", b"{}")
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            self.assertIn("Shared/servers.json", index)

    def test_traversal_path_rejected(self):
        zb = ZipBuilder().add_bytes("../evil", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_embedded_traversal_rejected(self):
        zb = ZipBuilder().add_bytes("ok/../../evil", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_absolute_path_rejected(self):
        zb = ZipBuilder().add_bytes("/etc/evil", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_drive_letter_rejected(self):
        zb = ZipBuilder().add_bytes("C:/Windows/evil", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_empty_name_rejected(self):
        zb = ZipBuilder().add_bytes("   ", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_duplicate_after_normalize_rejected(self):
        zb = ZipBuilder().add_bytes("Shared/servers.json", b"{}").add_bytes("Shared\\servers.json", b"{}")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_symlink_rejected(self):
        zb = ZipBuilder().add_bytes("Shared/servers.json", b"{}").add_symlink("evil")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_encrypted_member_rejected(self):
        zb = ZipBuilder().add_encrypted_flag("secret.json", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf)

    def test_member_count_limit(self):
        zb = ZipBuilder()
        for i in range(6):
            zb.add_bytes(f"f{i}.txt", b"x")
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf, max_members=5)

    def test_member_size_limit_declared(self):
        zb = ZipBuilder().add_bytes("big.json", b"B" * 1000)
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf, max_member_size=500)

    def test_total_size_limit_declared(self):
        zb = ZipBuilder().add_bytes("a.json", b"A" * 600).add_bytes("b.json", b"B" * 600)
        with self.assertRaises(ValueError), zb.open() as zf:
            bi.build_safe_zip_index(zf, max_member_size=1000, max_total_size=1000)

    def test_member_size_cap_enforced_on_real_read(self):
        # The helper must count the bytes it ACTUALLY reads (not trust the
        # declared file_size): a 5000-byte member cannot be read through a
        # 4999-byte cap, even though reading one more chunk would be harmless.
        zb = ZipBuilder().add_bytes("bomb.json", b"B" * 5000)
        with zb.open() as zf:
            info = zf.infolist()[0]
            self.assertEqual(int(info.file_size), 5000)
            with self.assertRaises(ValueError):
                bi.read_zip_member_bytes(zf, info, max_size=4999)
            # exactly at the cap -> allowed
            data = bi.read_zip_member_bytes(zf, info, max_size=5000)
            self.assertEqual(len(data), 5000)

    def test_copy_zip_member_respects_cap(self):
        zb = ZipBuilder().add_bytes("f.bin", b"C" * 1000)
        with zb.open() as zf:
            info = zf.infolist()[0]
            dst = self.dir / "out.bin"
            with self.assertRaises(ValueError):
                bi.copy_zip_member_to_file(zf, info, dst, max_size=100)

    def test_crc_check_rejects_corrupt_zip(self):
        # Build a real on-disk zip then corrupt a stored byte so CRC fails.
        p = self.dir / "corrupt.zip"
        db = _make_sqlite(self.dir / "src.db")
        with zipfile.ZipFile(p, mode="w") as zf:
            zf.write(db, arcname="Shared/hiddify_sellbot.db")
        raw = bytearray(p.read_bytes())
        # corrupt a byte in the middle of the file (inside member data)
        mid = len(raw) // 2
        raw[mid] ^= 0xFF
        p.write_bytes(bytes(raw))
        with zipfile.ZipFile(p, mode="r") as zf:
            with self.assertRaises(ValueError):
                bi.verify_zip_crc(zf)

    def test_crc_check_passes_healthy_zip(self):
        zb = ZipBuilder().add_bytes("a.json", b"{}")
        with zb.open() as zf:
            bi.verify_zip_crc(zf)  # must not raise


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _build_zip_with_manifest(self, manifest: dict, fill_hashes: bool = True) -> zipfile.ZipFile:
        """Build a zip with servers.json + plans.json and the given manifest.

        When fill_hashes is True, every manifest entry's sha256/size is
        replaced with the correct values of the stored members. When False,
        the caller-provided (possibly wrong) values are kept as-is.
        """
        servers_b = json.dumps({"servers": []}).encode()
        plans_b = json.dumps({"plans": {}}).encode()
        zb = ZipBuilder().add_bytes("Shared/servers.json", servers_b).add_bytes("Shared/plans.json", plans_b)
        with zb.open() as zf:
            infos = {info.filename: info for info in zf.infolist()}
            for entry in manifest.get("files", []):
                info = infos.get(entry["path"])
                if info is None:
                    continue
                if fill_hashes:
                    entry["size"] = int(info.file_size)
                    entry["sha256"] = hashlib.sha256(zf.read(info)).hexdigest()
        zb.add_bytes("Backup_Bot_x.json", json.dumps(manifest).encode())
        return zb.open()

    def test_valid_manifest_with_hashes_accepted(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 2,
            "files": [
                {"path": "Shared/servers.json", "sha256": None},
                {"path": "Shared/plans.json", "sha256": None},
            ],
        }
        with self._build_zip_with_manifest(manifest) as zf:
            index = bi.build_safe_zip_index(zf)
            result = bi.validate_backup_manifest(manifest, zf, index)
            self.assertEqual(result["entries"], 2)
            self.assertEqual(result["hashed"], 2)

    def test_wrong_hash_rejected(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 1,
            "files": [{"path": "Shared/servers.json", "sha256": "0" * 64}],
        }
        with self._build_zip_with_manifest(manifest, fill_hashes=False) as zf:
            index = bi.build_safe_zip_index(zf)
            with self.assertRaises(ValueError):
                bi.validate_backup_manifest(manifest, zf, index)

    def test_missing_member_rejected(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 2,
            "files": [
                {"path": "Shared/servers.json", "sha256": None},
                {"path": "Shared/plans.json", "sha256": None},
            ],
        }
        zb = ZipBuilder().add_bytes("Shared/servers.json", b"{}")
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            with self.assertRaises(ValueError):
                bi.validate_backup_manifest(manifest, zf, index)

    def test_legacy_manifest_without_sha256_accepted(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 1,
            "files": [{"path": "Shared/servers.json", "size": None}],
        }
        with self._build_zip_with_manifest(manifest, fill_hashes=False) as zf:
            index = bi.build_safe_zip_index(zf)
            # size None -> not validated; no sha256 -> no hashing
            result = bi.validate_backup_manifest(manifest, zf, index)
            self.assertEqual(result["entries"], 1)
            self.assertEqual(result["hashed"], 0)

    def test_size_mismatch_rejected(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 1,
            "files": [{"path": "Shared/servers.json", "size": 999}],
        }
        with self._build_zip_with_manifest(manifest, fill_hashes=False) as zf:
            index = bi.build_safe_zip_index(zf)
            with self.assertRaises(ValueError):
                bi.validate_backup_manifest(manifest, zf, index)

    def test_files_count_mismatch_rejected(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 5,
            "files": [{"path": "Shared/servers.json", "sha256": None}],
        }
        with self._build_zip_with_manifest(manifest) as zf:
            index = bi.build_safe_zip_index(zf)
            with self.assertRaises(ValueError):
                bi.validate_backup_manifest(manifest, zf, index)

    def test_extra_zip_members_do_not_fail(self):
        manifest = {
            "backup_type": "bot",
            "files_count": 1,
            "files": [{"path": "Shared/servers.json", "sha256": None}],
        }
        with self._build_zip_with_manifest(manifest) as zf:
            index = bi.build_safe_zip_index(zf)
            # index also contains Shared/plans.json which is NOT in the manifest
            self.assertGreater(len(index), 1)
            result = bi.validate_backup_manifest(manifest, zf, index)
            self.assertEqual(result["entries"], 1)

    def test_non_dict_manifest_rejected(self):
        zb = ZipBuilder().add_bytes("a.json", b"{}")
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            with self.assertRaises(ValueError):
                bi.validate_backup_manifest(["not", "a", "dict"], zf, index)

    def test_manifest_without_files_key_is_tolerated(self):
        # e.g. the "full" backup manifest which has no per-file list
        manifest = {"backup_type": "full", "panel_backups_count": 3}
        with ZipBuilder().add_bytes("a.json", b"{}").open() as zf:
            index = bi.build_safe_zip_index(zf)
            result = bi.validate_backup_manifest(manifest, zf, index)
            self.assertEqual(result["entries"], 0)

    def test_find_bot_manifest_member_ignores_receipts(self):
        zb = ZipBuilder() \
            .add_bytes("Receiptions/Backup_Bot_fake.json", b"{}") \
            .add_bytes("Backup_Bot_01-01-2026_00-00-00.json", b"{}")
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            self.assertEqual(
                bi.find_bot_manifest_member(index),
                "Backup_Bot_01-01-2026_00-00-00.json",
            )


class BackupTypeDetectionTests(unittest.TestCase):
    def test_bot_manifest_type_detected(self):
        zb = ZipBuilder().add_bytes("Backup_Bot_a.json", json.dumps({"backup_type": "bot"}).encode())
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            name = bi.find_bot_manifest_member(index)
            data = json.loads(zf.read(index[name]).decode())
            self.assertEqual(data.get("backup_type"), "bot")

    def test_full_backup_with_bot_manifest_inside_is_still_indexable(self):
        # A full backup contains the bot manifest inside — both must be found.
        zb = ZipBuilder() \
            .add_bytes("Shared/servers.json", b"{}") \
            .add_bytes("Backup_Bot_inner.json", json.dumps({"backup_type": "bot", "files": []}).encode()) \
            .add_bytes("Backup_All_outer.json", json.dumps({"backup_type": "full"}).encode())
        with zb.open() as zf:
            index = bi.build_safe_zip_index(zf)
            self.assertEqual(bi.find_bot_manifest_member(index), "Backup_Bot_inner.json")
            self.assertIn("Backup_All_outer.json", index)


class ReceiptsOnlyRestoreTests(unittest.TestCase):
    """A ZIP containing only Receiptions/ must be restorable on its own, and
    the new bot backup builder must always emit a complete sha256 manifest.

    These tests exercise the real AdminBot.userbot functions without
    Telegram: telegram-related modules are stubbed in sys.modules and the
    real module file is loaded via importlib. _project_root_dir and
    _backup_storage_dir are pointed at a TemporaryDirectory so no real
    project file (and no real backups/ dir) is ever touched.
    """

    @classmethod
    def setUpClass(cls):
        import sys
        import types as _types
        import importlib.util

        if "AdminBot.userbot" in sys.modules and hasattr(sys.modules["AdminBot.userbot"], "_restore_from_zip_backup"):
            cls.ub = sys.modules["AdminBot.userbot"]
            return

        pkg = _types.ModuleType("AdminBot"); pkg.__path__ = []
        kb = _types.ModuleType("AdminBot.keyboards"); kb.admin_main_keyboard = object()
        tbs = _types.ModuleType("Shared.tg_button_styles")
        tbs.BUTTON_STYLE_THEMES = {}
        tbs.normalize_button_theme = lambda x: x
        tbs.inline_button = object
        tbs.keyboard_button = object
        udb = _types.ModuleType("Shared.userbot_db"); udb.init_db = lambda: None
        sdb = _types.ModuleType("Shared.database"); sdb.get_servers = lambda: []
        hap = _types.ModuleType("Shared.hiddify_api")
        for n in ("HiddifyApiError", "XuiApiError"):
            setattr(hap, n, type(n, (Exception,), {}))

        tg = _types.ModuleType("telegram")
        for n in ("Update", "Bot", "BotCommand", "KeyboardButton", "InlineKeyboardMarkup",
                  "InlineKeyboardButton", "ReplyKeyboardMarkup", "ReplyKeyboardRemove",
                  "InputMediaPhoto"):
            setattr(tg, n, type(n, (), {}))
        terr = _types.ModuleType("telegram.error")
        for n in ("TelegramError", "NetworkError", "TimedOut", "BadRequest", "Forbidden"):
            setattr(terr, n, type(n, (Exception,), {}))
        ext = _types.ModuleType("telegram.ext")
        for n in ("Application", "ApplicationBuilder", "ApplicationHandlerStop",
                  "CommandHandler", "MessageHandler", "CallbackQueryHandler", "ContextTypes"):
            setattr(ext, n, object)
        class _F:
            ALL = TEXT = PHOTO = COMMAND = object()
        ext.filters = _F

        saved = {n: sys.modules.get(n) for n in (
            "AdminBot", "AdminBot.keyboards", "Shared.tg_button_styles",
            "Shared.userbot_db", "Shared.database", "Shared.hiddify_api",
            "telegram", "telegram.error", "telegram.ext")}
        cls._saved_modules = saved
        sys.modules.update({
            "AdminBot": pkg, "AdminBot.keyboards": kb,
            "Shared.tg_button_styles": tbs, "Shared.userbot_db": udb,
            "Shared.database": sdb, "Shared.hiddify_api": hap,
            "telegram": tg, "telegram.error": terr, "telegram.ext": ext,
        })
        cls._stubs_applied = True

        try:
            spec = importlib.util.spec_from_file_location(
                "AdminBot.userbot", Path(__file__).resolve().parents[1] / "AdminBot" / "userbot.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["AdminBot.userbot"] = mod
            spec.loader.exec_module(mod)
            cls.ub = mod
        finally:
            # Restore any pre-existing real modules; keep the stubs only where
            # nothing existed before (tests never import telegram for real).
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._orig_root = self.ub._project_root_dir
        self.ub._project_root_dir = lambda: self.root
        self.addCleanup(lambda: setattr(self.ub, "_project_root_dir", self._orig_root))
        self._orig_storage = self.ub._backup_storage_dir
        backup_root = self.root / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        self.ub._backup_storage_dir = lambda: backup_root
        self.addCleanup(lambda: setattr(self.ub, "_backup_storage_dir", self._orig_storage))

    @classmethod
    def tearDownClass(cls):
        import sys
        sys.modules.pop("AdminBot.userbot", None)

    def test_receipts_only_zip_restored(self):
        shared = self.root / "Shared"
        shared.mkdir()
        (shared / "servers.json").write_text('{"keep": true}', encoding="utf-8")
        receipts = self.root / "Receiptions"
        receipts.mkdir()
        (receipts / "old.png").write_bytes(b"old-bytes")

        zip_path = self.root / "receipts.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Receiptions/r1.png", b"\x89PNG-1")
            zf.writestr("Receiptions/sub/r2.png", b"\x89PNG-2")
            zf.writestr("Receiptions/r3.txt", b"text receipt")

        result = self.ub._restore_from_zip_backup(zip_path)

        self.assertEqual(result["mode"], "zip")
        self.assertEqual(result["receipts_count"], 3)
        self.assertEqual(result["restored_files"], ["Receiptions/*"])
        self.assertEqual((receipts / "r1.png").read_bytes(), b"\x89PNG-1")
        self.assertEqual((receipts / "sub" / "r2.png").read_bytes(), b"\x89PNG-2")
        self.assertEqual((receipts / "r3.txt").read_bytes(), b"text receipt")
        # untouched files stay untouched
        self.assertEqual((shared / "servers.json").read_text(encoding="utf-8"), '{"keep": true}')

    def test_receipts_restored_alongside_db(self):
        shared = self.root / "Shared"
        shared.mkdir()
        db = _make_sqlite(shared / "hiddify_sellbot.db")

        zip_path = self.root / "mixed.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(db, arcname="Shared/hiddify_sellbot.db")
            zf.writestr("Receiptions/r1.png", b"png")

        result = self.ub._restore_from_zip_backup(zip_path)
        self.assertIn("Shared/hiddify_sellbot.db", result["restored_files"])
        self.assertIn("Receiptions/*", result["restored_files"])
        self.assertEqual(result["receipts_count"], 1)

    def test_empty_zip_still_raises(self):
        zip_path = self.root / "empty.zip"
        with zipfile.ZipFile(zip_path, "w"):
            pass
        with self.assertRaises(ValueError):
            self.ub._restore_from_zip_backup(zip_path)

    def test_traversal_member_in_receipts_zip_rejected_before_write(self):
        receipts = self.root / "Receiptions"
        receipts.mkdir()
        (receipts / "sentinel.png").write_bytes(b"sentinel")

        zip_path = self.root / "attack.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Receiptions/ok.png", b"fine")
            zf.writestr("Receiptions/../../evil.png", b"bad")

        with self.assertRaises(ValueError):
            self.ub._restore_from_zip_backup(zip_path)

        # nothing was written and the destination was not replaced
        self.assertEqual(sorted(p.name for p in receipts.iterdir()), ["sentinel.png"])
        self.assertFalse((self.root / "evil.png").exists())

    # ---- builder strictness: new backups must always carry sha256 ----

    def _seed_project(self):
        shared = self.root / "Shared"
        shared.mkdir(parents=True, exist_ok=True)
        db = _make_sqlite(shared / "hiddify_sellbot.db")
        (shared / "servers.json").write_text('{"servers": []}', encoding="utf-8")
        (shared / "plans.json").write_text('{"plans": {}}', encoding="utf-8")
        receipts = self.root / "Receiptions"
        receipts.mkdir(exist_ok=True)
        (receipts / "r1.png").write_bytes(b"\x89PNG-1")
        return db

    def test_new_backup_manifest_entries_all_have_sha256(self):
        self._seed_project()

        with patch.object(self.ub, "_checkpoint_sqlite"):
            out = self.ub._make_bot_backup_zip()

        self.assertTrue(out.exists())
        with zipfile.ZipFile(out) as zf:
            index = bi.build_safe_zip_index(zf)
            m_name = bi.find_bot_manifest_member(index)
            self.assertTrue(m_name)
            manifest = json.loads(zf.read(index[m_name]).decode("utf-8"))
            self.assertEqual(manifest["backup_type"], "bot")
            self.assertEqual(manifest["files_count"], len(manifest["files"]))
            self.assertGreater(len(manifest["files"]), 0)
            for entry in manifest["files"]:
                self.assertIn("path", entry)
                self.assertIn("size", entry)
                sha = entry.get("sha256")
                self.assertIsInstance(sha, str)
                self.assertEqual(len(sha), 64)
                int(sha, 16)  # valid hex
            result = bi.validate_backup_manifest(manifest, zf, index)
            self.assertEqual(result["hashed"], len(manifest["files"]))

    def test_builder_missing_member_raises_and_cleans_up(self):
        self._seed_project()
        # Force the index lookup to lose a member during the manifest phase.
        real_norm = bi.normalize_member_name

        def broken_norm(name):
            # The main db "disappears" from the archive view
            if str(name).endswith("hiddify_sellbot.db"):
                return ""
            return real_norm(name)

        with patch.object(self.ub, "_checkpoint_sqlite"), \
             patch.object(bi, "normalize_member_name", broken_norm):
            with self.assertRaises(ValueError):
                self.ub._make_bot_backup_zip()

        leftovers = list((self.root / "backups").glob("Backup_Bot_*.zip"))
        self.assertEqual(leftovers, [], "incomplete zip must be removed")

    def test_builder_hash_failure_raises_and_cleans_up(self):
        self._seed_project()
        # Make sha256_zip_member fail once for the main db member.
        real_hash = bi.sha256_zip_member
        calls = {"n": 0}

        def flaky_hash(zf, info, **kw):
            if str(info.filename).endswith("hiddify_sellbot.db"):
                calls["n"] += 1
                raise RuntimeError("simulated hashing failure")
            return real_hash(zf, info, **kw)

        with patch.object(self.ub, "_checkpoint_sqlite"), \
             patch.object(bi, "sha256_zip_member", flaky_hash):
            with self.assertRaises(RuntimeError):
                self.ub._make_bot_backup_zip()

        self.assertEqual(calls["n"], 1)
        leftovers = list((self.root / "backups").glob("Backup_Bot_*.zip"))
        self.assertEqual(leftovers, [], "incomplete zip must be removed")


if __name__ == "__main__":
    unittest.main()
