"""Secret-integrity tests (stage 7): .env atomic writes, file permissions
and secret redaction. Only stdlib + project utilities are used; no real
.env, token, database or network is ever touched.
"""

import importlib.util
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Shared import secure_io
from Shared.secure_io import (
    atomic_update_env,
    ensure_private_file,
    redact_sensitive_text,
    safe_exception_name,
    validate_env_key,
    validate_env_value,
)

FAKE_TOKEN = "123456789:AAf4kEXAMPLE-TOKEN-not-real-1234567890"


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class AtomicUpdateEnvTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _env(self, content: str = "") -> Path:
        return _write(self.dir / ".env", content)

    def test_new_file_created_with_mode_600(self):
        p = self.dir / ".env"
        atomic_update_env(p, {"NEW_KEY": "value"})
        self.assertTrue(p.exists())
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertIn("NEW_KEY=value", p.read_text(encoding="utf-8"))

    def test_update_existing_key(self):
        p = self._env("A=1\nB=old\n")
        atomic_update_env(p, {"B": "new"})
        content = p.read_text(encoding="utf-8")
        self.assertIn("B=new", content)
        self.assertNotIn("B=old", content)
        self.assertIn("A=1", content)

    def test_add_new_key(self):
        p = self._env("A=1\n")
        atomic_update_env(p, {"B": "2"})
        content = p.read_text(encoding="utf-8")
        self.assertIn("A=1", content)
        self.assertIn("B=2", content)

    def test_comments_blank_lines_and_other_keys_preserved(self):
        original = (
            "# top comment\n"
            "\n"
            "A=1\n"
            "  # indented comment\n"
            "B=old\n"
            "C=3\n"
        )
        p = self._env(original)
        atomic_update_env(p, {"B": "new"})
        content = p.read_text(encoding="utf-8")
        self.assertIn("# top comment", content)
        self.assertIn("  # indented comment", content)
        self.assertIn("A=1", content)
        self.assertIn("C=3", content)
        self.assertIn("B=new", content)
        self.assertNotIn("B=old", content)

    def test_duplicate_keys_collapsed(self):
        p = self._env("B=old\nA=1\nB=dup\n")
        atomic_update_env(p, {"B": "new"})
        content = p.read_text(encoding="utf-8")
        self.assertEqual(content.count("B="), 1)
        self.assertIn("B=new", content)
        self.assertIn("A=1", content)

    def test_invalid_key_rejected(self):
        p = self._env("A=1\n")
        before = p.read_bytes()
        for bad in ("bad key", "1BAD", "KEY-DASH", "KEY;x", ""):
            with self.assertRaises(ValueError):
                atomic_update_env(p, {bad: "v"})
        self.assertEqual(p.read_bytes(), before)

    def test_value_with_newline_cr_or_nul_rejected(self):
        p = self._env("A=1\n")
        before = p.read_bytes()
        for bad in ("line1\nEVIL=1", "line1\rEVIL=1", "a\x00b"):
            with self.assertRaises(ValueError):
                atomic_update_env(p, {"X": bad})
        self.assertEqual(p.read_bytes(), before)

    def test_original_file_untouched_on_validation_error(self):
        p = self._env("SECRET=keepme\nOTHER=ok\n")
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            atomic_update_env(p, {"GOOD": "v", "BAD KEY": "x"})
        self.assertEqual(p.read_bytes(), before)
        self.assertFalse(list(self.dir.glob(".env.*tmp*")), "temp file must be cleaned")

    def test_replace_failure_leaves_original_intact(self):
        p = self._env("A=1\n")
        before = p.read_bytes()
        real_replace = os.replace
        calls = []

        def failing_replace(src, dst):
            calls.append(1)
            raise OSError("simulated replace failure")

        with patch.object(os, "replace", failing_replace):
            with self.assertRaises(OSError):
                atomic_update_env(p, {"A": "2"})
        self.assertEqual(calls, [1])
        self.assertEqual(p.read_bytes(), before)
        # temp file cleaned up (the .env.lock sidecar may remain — by design)
        leftovers = [x for x in self.dir.iterdir()
                     if x.name.startswith(".env.") and not x.name.endswith(".lock")]
        self.assertEqual(leftovers, [])

    def test_concurrent_threads_do_not_lose_updates(self):
        p = self._env("BASE=0\n")
        threads = []
        results = []

        def worker(key, value):
            try:
                atomic_update_env(p, {key: value})
                results.append(key)
            except Exception as e:  # pragma: no cover
                results.append(f"ERROR:{e}")

        for i in range(8):
            t = threading.Thread(target=worker, args=(f"KEY_{i}", str(i)))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        content = p.read_text(encoding="utf-8")
        for i in range(8):
            self.assertIn(f"KEY_{i}={i}", content, f"update {i} lost; content={content!r}")
        self.assertEqual(len(results), 8)

    def test_lock_file_created_beside_env(self):
        p = self._env("A=1\n")
        atomic_update_env(p, {"B": "2"})
        self.assertTrue((self.dir / ".env.lock").exists())

    # ---- stage-7-remediation: whitespace & directory-mode invariants ----

    def test_trailing_blank_lines_preserved_exactly(self):
        p = self._env("A=1\n\n\n")
        atomic_update_env(p, {"A": "2"})
        self.assertEqual(p.read_text(encoding="utf-8"), "A=2\n\n\n")

    def test_missing_final_newline_preserved(self):
        p = self._env("A=1\nB=2")
        atomic_update_env(p, {"A": "9"})
        self.assertEqual(p.read_text(encoding="utf-8"), "A=9\nB=2")

    def test_middle_comments_blanks_unrelated_keys_preserved(self):
        src = "# c\n\nA=1\n  # i\nB=old\nC=3\n\n# tail\n"
        p = self._env(src)
        atomic_update_env(p, {"B": "new"})
        content = p.read_text(encoding="utf-8")
        self.assertEqual(content.count("B="), 1)
        self.assertIn("B=new", content)
        self.assertIn("# c", content)
        self.assertIn("  # i", content)
        self.assertIn("C=3", content)
        self.assertIn("\n\n# tail\n", content)

    def test_new_file_gets_wellformed_ending(self):
        p = self.dir / "fresh.env"
        atomic_update_env(p, {"X": "1"})
        self.assertEqual(p.read_text(encoding="utf-8"), "X=1\n")

    def test_parent_directory_mode_untouched(self):
        sub = self.dir / "sub0755"
        sub.mkdir(mode=0o755)
        os.chmod(sub, 0o755)
        env_file = sub / ".env"
        atomic_update_env(env_file, {"K": "v"})
        self.assertEqual(stat.S_IMODE(sub.stat().st_mode), 0o755,
                         "atomic_update_env must NOT change the parent dir mode")
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((sub / ".env.lock").stat().st_mode), 0o600)

    def test_replace_failure_leaves_no_temp_and_original_intact_again(self):
        p = self._env("A=1\n")
        before = p.read_bytes()

        def failing_replace(src, dst):
            raise OSError("simulated replace failure 2")

        with patch.object(os, "replace", failing_replace):
            with self.assertRaises(OSError):
                atomic_update_env(p, {"A": "3"})
        self.assertEqual(p.read_bytes(), before)
        leftovers = [x for x in self.dir.iterdir()
                     if x.name.startswith(".env.") and x.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_final_mode_is_600_even_if_file_pre_existed(self):
        p = self._env("A=1\n")
        os.chmod(p, 0o644)
        atomic_update_env(p, {"A": "2"})
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_ensure_private_file_sets_600(self):
        p = self.dir / "file.txt"
        p.write_text("x")
        os.chmod(p, 0o644)
        self.assertTrue(ensure_private_file(p))
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_ensure_private_file_missing_is_noop(self):
        self.assertFalse(ensure_private_file(self.dir / "nope.txt"))

    def _make_db(self, path: Path):
        import sqlite3
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE t (k TEXT)")
        conn.execute("INSERT INTO t VALUES ('v')")
        conn.commit()
        conn.close()

    def test_temp_agency_db_gets_600_on_connection(self):
        """Shared.agent_db._get_conn must tighten permissions (temp DB)."""
        from Shared import agent_db
        db = self.dir / "agency.db"
        self._make_db(db)
        with patch.object(agent_db, "DB_PATH", db), \
             patch.object(agent_db, "_db_initialized", True), \
             patch.object(agent_db, "_init_db_path", str(db)):
            conn = agent_db._get_conn()
            conn.close()
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)

    def test_temp_userbot_db_gets_600_on_connection(self):
        from Shared import userbot_db
        db = self.dir / "hiddify_sellbot.db"
        self._make_db(db)
        with patch.object(userbot_db, "DB_PATH", db):
            conn = userbot_db._get_conn()
            conn.close()
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)

    def test_temp_customer_db_gets_600_on_connection(self):
        from CustomerBot import database as cdb
        db = self.dir / "customer_bot.db"
        self._make_db(db)
        with patch.object(cdb, "DB_PATH", db):
            conn = cdb._get_conn()
            conn.close()
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)

    def test_temp_agentbot_db_gets_600_on_connection(self):
        from AgentBot import database as adb
        db = self.dir / "agent_bot.db"
        self._make_db(db)
        with patch.object(adb, "DB_FILE", db):
            conn = adb._conn()
            conn.close()
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)


class RedactionTests(unittest.TestCase):
    def test_telegram_token_fully_redacted(self):
        text = f"request failed for bot {FAKE_TOKEN} with code 404"
        out = redact_sensitive_text(text)
        self.assertNotIn(FAKE_TOKEN, out)
        self.assertNotIn("AAf4kEXAMPLE", out)
        self.assertNotIn("123456789:", out)
        self.assertIn("<redacted-token>", out)

    def test_telegram_token_redacted_inside_api_url(self):
        for method in ("getMe", "sendMessage?chat_id=1"):
            text = f"https://api.telegram.org/bot{FAKE_TOKEN}/{method}"
            out = redact_sensitive_text(text)
            self.assertNotIn(FAKE_TOKEN, out)
            self.assertNotIn("AAf4kEXAMPLE", out)
            self.assertNotIn("123456789:", out)
            self.assertIn("bot<redacted-token>", out)

    def test_telegram_token_url_redaction_handles_secret_end_characters(self):
        for suffix in ("-", "_"):
            token = "123456789:" + ("A" * 30) + suffix
            out = redact_sensitive_text(
                f"https://api.telegram.org/bot{token}/getMe"
            )
            self.assertNotIn(token, out)
            self.assertNotIn("123456789:", out)
            self.assertEqual(
                out,
                "https://api.telegram.org/bot<redacted-token>/getMe",
            )

    def test_bearer_token_redacted(self):
        out = redact_sensitive_text("Authorization: Bearer sk-live-abcdef1234567890")
        self.assertNotIn("sk-live-abcdef1234567890", out)
        self.assertIn("<redacted>", out)

    def test_authorization_header_redacted(self):
        out = redact_sensitive_text('{"Authorization": "Basic dXNlcjpwYXNzd29yZDEyMw=="}')
        self.assertNotIn("dXNlcjpwYXNzd29yZDEyMw==", out)

    def test_webhook_secret_redacted(self):
        out = redact_sensitive_text("webhook_secret=supersecretvalue123")
        self.assertNotIn("supersecretvalue123", out)

    def test_generic_secret_kv_redacted(self):
        out = redact_sensitive_text("api_key=abcd1234efgh; other=ok")
        self.assertNotIn("abcd1234efgh", out)
        self.assertIn("other=ok", out)

    def test_normal_text_untouched(self):
        text = "service #12 updated, agent=5, receipts_count=3"
        self.assertEqual(redact_sensitive_text(text), text)

    def test_safe_exception_name_has_no_message(self):
        try:
            raise RuntimeError(f"invalid token: {FAKE_TOKEN}")
        except RuntimeError as e:
            name = safe_exception_name(e)
        self.assertEqual(name, "RuntimeError")
        self.assertNotIn("AAf4kEXAMPLE", name)

class _StubHelper:
    """Shared helper to stub telegram modules for module imports."""
    _saved = {}
    _installed = {}

    @classmethod
    def install(cls, extra_env=None):
        import types
        cls._saved = {n: sys.modules.get(n) for n in
                      ("telegram", "telegram.error", "telegram.ext", "telegram.request",
                       "Shared.tg_button_styles")}

        tg = types.ModuleType("telegram")
        tg.__version__ = "20.0"

        class _Any:
            def __init__(self, *a, **k):
                pass

            def __call__(self, *a, **k):
                # Builder-style calls must return an object whose every
                # attribute resolves again (chainable stub).
                return self

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)
                return self

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def __iter__(self):
                return iter([])

            def __setitem__(self, key, value):
                setattr(self, f"_item_{key}", value)

            def __getitem__(self, key):
                return getattr(self, f"_item_{key}", None)

            def __contains__(self, key):
                return True

            def __or__(self, other):
                return self

            def __and__(self, other):
                return self

            def __invert__(self):
                return self

        cls._Any = _Any
        for n in ("Update", "Bot", "BotCommand", "InlineKeyboardMarkup", "InlineKeyboardButton",
                  "ReplyKeyboardMarkup", "ReplyKeyboardRemove", "InputMediaPhoto",
                  "MenuButtonCommands", "KeyboardButton"):
            setattr(tg, n, _Any)
        terr = types.ModuleType("telegram.error")
        for n in ("TelegramError", "BadRequest", "Forbidden", "NetworkError", "TimedOut",
                  "Conflict"):
            setattr(terr, n, type(n, (Exception,), {}))
        ext = types.ModuleType("telegram.ext")
        ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})
        for n in ("Application", "ApplicationBuilder", "CommandHandler",
                  "MessageHandler", "CallbackQueryHandler"):
            setattr(ext, n, _Any)
        ext.filters = types.SimpleNamespace(
            ALL=_Any(), TEXT=_Any(), PHOTO=_Any(), COMMAND=_Any(),
            Regex=lambda pattern, **kw: _Any(),
        )
        req = types.ModuleType("telegram.request")
        req.HTTPXRequest = _Any
        tbs = types.ModuleType("Shared.tg_button_styles")
        tbs.BUTTON_STYLE_THEMES = {}
        tbs.normalize_button_theme = lambda x: x
        tbs.inline_button = _Any
        tbs.keyboard_button = _Any
        cls._installed = {
            "telegram": tg, "telegram.error": terr, "telegram.ext": ext,
            "telegram.request": req, "Shared.tg_button_styles": tbs,
        }
        sys.modules.update(cls._installed)
        if extra_env:
            cls._env = patch.dict(os.environ, extra_env, clear=False)
            cls._env.start()
        else:
            cls._env = None

    @classmethod
    def restore(cls, module_names):
        if cls._env is not None:
            cls._env.stop()
            cls._env = None
        # Only restore what install() actually replaced; transitively imported
        # real modules (e.g. AgentBot.*, Shared.agent_db) must stay so later
        # test files keep working.
        for n, m in cls._saved.items():
            if m is not None:
                sys.modules[n] = m
        for n in module_names:
            sys.modules.pop(n, None)


class TicketLogRedactionTests(unittest.TestCase):
    """The ticket download failure path must never log the bot token."""

    @classmethod
    def setUpClass(cls):
        _StubHelper.install()
        spec = importlib.util.spec_from_file_location(
            "AgentBot.handlers.tickets", PROJECT_ROOT / "AgentBot" / "handlers" / "tickets.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["AgentBot.handlers.tickets"] = cls.mod
        spec.loader.exec_module(cls.mod)

    @classmethod
    def tearDownClass(cls):
        _StubHelper.restore(["AgentBot.handlers.tickets"])

    def test_download_failure_log_has_no_token(self):
        import logging
        token = FAKE_TOKEN
        code, msg_id = 101, 202

        class _BrokenBot:
            def __init__(self, token=None, request=None):
                pass

            async def get_file(self, fid):
                raise RuntimeError(f"401 Unauthorized for url .../bot{token}/getFile")

        import asyncio

        class _Msg:
            async def reply_photo(self, *a, **k):
                raise AssertionError("should not be reached")

            async def reply_text(self, *a, **k):
                pass

        class _Upd:
            message = _Msg()

        class _Ctx:
            user_data = {}

        rows = [{"id": msg_id, "photo_file_id": "fake-file-id"}]
        with patch.object(self.mod, "Bot", _BrokenBot), \
             patch.dict(sys.modules, {"telegram.request": types.SimpleNamespace(HTTPXRequest=lambda **k: object())}), \
             patch("Shared.agent_db.get_all_active_customer_bots",
                   lambda: [{"agent_id": 5, "bot_token": token}], create=True), \
             patch.object(self.mod, "get_agent_id", lambda ctx: 5), \
             patch.object(self.mod, "get_customer_ticket_messages", lambda a, c: rows), \
             patch.object(self.mod, "get_customer_ticket", lambda a, c: None):
            with self.assertLogs("AgentBot.handlers.tickets", level="WARNING") as captured:
                coro = self.mod.handle_ticket_shot_start(
                    _Upd(), _Ctx(), f"tshotu_{code}_{msg_id}",
                )
                asyncio.run(coro)

        all_logs = "\n".join(captured.output)
        self.assertNotIn(token, all_logs, "token leaked into logs")
        self.assertNotIn("AAf4kEXAMPLE", all_logs)
        self.assertIn(f"code={code}", all_logs)
        self.assertIn("agent=5", all_logs)


class CustomerBotInvalidTokenLogTests(unittest.TestCase):
    """CustomerBot's invalid-token path must not log str(exception) raw."""

    def test_invalid_token_log_redacted(self):
        import asyncio
        _StubHelper.install(extra_env={"USER_BOT_TOKEN": "x", "ADMIN_ID": "0"})
        try:
            spec = importlib.util.spec_from_file_location(
                "CustomerBot.main", PROJECT_ROOT / "CustomerBot" / "main.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["CustomerBot.main"] = mod
            spec.loader.exec_module(mod)

            class _FakeBuilder:
                def __init__(self):
                    self.calls = []

                def __getattr__(self, name):
                    def _chain(*a, **k):
                        self.calls.append(name)
                        return self
                    return _chain

                def build(self):
                    app = types.SimpleNamespace(
                        bot=types.SimpleNamespace(),
                        bot_data={},
                        add_handler=lambda *a, **k: None,
                        add_error_handler=lambda *a, **k: None,
                        updater=None,
                    )

                    async def get_me():
                        raise RuntimeError(f"Invalid token: {FAKE_TOKEN}")

                    app.bot.get_me = get_me
                    return app

            async def run():
                await mod.run_single_bot("tok", 42)

            with patch.object(mod, "ApplicationBuilder", _FakeBuilder), \
                 patch.object(mod, "_post_init", lambda app: asyncio.sleep(0)):
                with self.assertLogs("CustomerBot.Main", level="ERROR") as captured:
                    asyncio.run(run())

            all_logs = "\n".join(captured.output)
            self.assertNotIn(FAKE_TOKEN, all_logs)
            self.assertIn("RuntimeError", all_logs)
            self.assertIn("Agent #42", all_logs)
        finally:
            _StubHelper.restore(["CustomerBot.main"])


class WriterCompatibilityTests(unittest.TestCase):
    """All three .env writers must behave via the shared utility and never
    touch the real project .env."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.env_file = _write(self.dir / ".env", "OLD=1\n# keep me\n")

    def _load_admin_userbot(self):
        import types
        saved = {n: sys.modules.get(n) for n in
                 ("telegram", "telegram.error", "telegram.ext", "telegram.request",
                  "AdminBot", "AdminBot.keyboards", "Shared.tg_button_styles",
                  "Shared.userbot_db", "Shared.database", "Shared.hiddify_api",
                  "AdminBot.userbot")}
        tg = types.ModuleType("telegram")
        tg.__version__ = "20.0"
        class _Any:
            def __init__(self, *a, **k):
                pass
        for n in ("Update", "Bot", "BotCommand", "InlineKeyboardMarkup", "InlineKeyboardButton",
                  "ReplyKeyboardMarkup", "ReplyKeyboardRemove", "InputMediaPhoto"):
            setattr(tg, n, _Any)
        terr = types.ModuleType("telegram.error")
        for n in ("TelegramError", "BadRequest", "Forbidden", "NetworkError", "TimedOut"):
            setattr(terr, n, type(n, (Exception,), {}))
        ext = types.ModuleType("telegram.ext")
        ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})
        for n in ("Application", "ApplicationBuilder", "CommandHandler",
                  "MessageHandler", "CallbackQueryHandler"):
            setattr(ext, n, _Any)
        req = types.ModuleType("telegram.request")
        req.HTTPXRequest = _Any
        pkg = types.ModuleType("AdminBot"); pkg.__path__ = []
        kb = types.ModuleType("AdminBot.keyboards"); kb.admin_main_keyboard = _Any
        tbs = types.ModuleType("Shared.tg_button_styles")
        tbs.BUTTON_STYLE_THEMES = {}
        tbs.normalize_button_theme = lambda x: x
        tbs.inline_button = _Any
        tbs.keyboard_button = _Any
        udb = types.ModuleType("Shared.userbot_db"); udb.init_db = lambda: None
        sdb = types.ModuleType("Shared.database"); sdb.get_servers = lambda: []
        hap = types.ModuleType("Shared.hiddify_api")
        hap.HiddifyApiError = type("HiddifyApiError", (Exception,), {})
        hap.XuiApiError = type("XuiApiError", (Exception,), {})
        for n, m in {"telegram": tg, "telegram.error": terr, "telegram.ext": ext,
                     "telegram.request": req, "AdminBot": pkg, "AdminBot.keyboards": kb,
                     "Shared.tg_button_styles": tbs, "Shared.userbot_db": udb,
                     "Shared.database": sdb, "Shared.hiddify_api": hap}.items():
            sys.modules[n] = m
        try:
            spec = importlib.util.spec_from_file_location(
                "AdminBot.userbot", PROJECT_ROOT / "AdminBot" / "userbot.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["AdminBot.userbot"] = mod
            spec.loader.exec_module(mod)
            return mod, saved
        except BaseException:
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
            raise

    def test_admin_userbot_writer_uses_shared_atomic_utility(self):
        mod, saved = self._load_admin_userbot()
        try:
            # ENV_FILE points into the real project — redirect to temp.
            with patch.object(mod, "ENV_FILE", self.env_file):
                mod._write_env_values({"OLD": "2", "NEW": "x"})
            content = self.env_file.read_text(encoding="utf-8")
            self.assertIn("OLD=2", content)
            self.assertIn("NEW=x", content)
            self.assertIn("# keep me", content)
            self.assertEqual(
                stat.S_IMODE(self.env_file.stat().st_mode), 0o600,
                "writer must enforce 0600 via the shared utility",
            )
        finally:
            sys.modules.pop("AdminBot.userbot", None)
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)

    def test_agencies_writer_uses_shared_atomic_utility(self):
        """_update_env_file must delegate to secure_io.atomic_update_env with
        AGENT_BOT_TOKEN and return True on success / False on failure. The
        real .env is never touched (spy + temp dir)."""
        _StubHelper.install(extra_env={})
        try:
            spec = importlib.util.spec_from_file_location(
                "AdminBot.agencies", PROJECT_ROOT / "AdminBot" / "agencies.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["AdminBot.agencies"] = mod
            spec.loader.exec_module(mod)

            calls = []
            real_atomic = secure_io.atomic_update_env

            def spy(path, updates):
                calls.append((str(path), dict(updates)))
                # redirect the actual write to the temp .env
                return real_atomic(Path(self.dir / ".env"), updates)

            with patch.object(mod.secure_io, "atomic_update_env", spy):
                ok = mod._update_env_file(FAKE_TOKEN)

            self.assertTrue(ok)
            self.assertEqual(len(calls), 1)
            path_arg, updates = calls[0]
            self.assertTrue(path_arg.endswith(".env"))
            self.assertEqual(updates, {"AGENT_BOT_TOKEN": FAKE_TOKEN})
            # the redirected write went to the temp file with strict format
            self.assertIn(f"AGENT_BOT_TOKEN={FAKE_TOKEN}",
                          (self.dir / ".env").read_text(encoding="utf-8"))
        finally:
            _StubHelper.restore(["AdminBot.agencies"])

    def test_agentbot_payment_writer_uses_shared_atomic_utility(self):
        import types
        saved = {n: sys.modules.get(n) for n in
                 ("telegram", "telegram.error", "telegram.ext", "telegram.request",
                  "AgentBot.constants", "AgentBot.handlers", "AgentBot.handlers.base",
                  "AgentBot.keyboards", "Shared.tg_button_styles", "Shared.secure_io",
                  "AgentBot.utils.helpers", "AgentBot.database",
                  "AgentBot.handlers.settings_payment")}
        tg = types.ModuleType("telegram")
        tg.__version__ = "20.0"
        class _Any:
            def __init__(self, *a, **k):
                pass
        for n in ("Update", "Bot", "BotCommand", "InlineKeyboardMarkup", "InlineKeyboardButton",
                  "ReplyKeyboardMarkup", "InputMediaPhoto"):
            setattr(tg, n, _Any)
        terr = types.ModuleType("telegram.error")
        for n in ("TelegramError", "BadRequest", "Forbidden", "NetworkError", "TimedOut"):
            setattr(terr, n, type(n, (Exception,), {}))
        ext = types.ModuleType("telegram.ext")
        ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})
        for n in ("Application", "ApplicationBuilder", "CommandHandler",
                  "MessageHandler", "CallbackQueryHandler"):
            setattr(ext, n, _Any)
        req = types.ModuleType("telegram.request")
        req.HTTPXRequest = _Any
        tbs = types.ModuleType("Shared.tg_button_styles"); tbs.inline_button = _Any
        consts = types.ModuleType("AgentBot.constants")
        for name in ("UD_STATE", "UD_SELECTED_CARD", "STATE_ADD_CARD", "STATE_ADD_CARD_NUMBER",
                     "STATE_ADD_CARD_OWNER", "STATE_ADD_CARD_BANK", "STATE_EDIT_CARD",
                     "STATE_SET_CARD_TEXT"):
            setattr(consts, name, name)
        base = types.ModuleType("AgentBot.handlers.base"); base.get_agent_id = lambda ctx: 1
        kbmod = types.ModuleType("AgentBot.keyboards")
        for fn in ("card_settings_keyboard", "cancel_keyboard", "main_menu_keyboard",
                   "payment_cards_list_keyboard", "sms_webhook_settings_keyboard", "_ikb"):
            setattr(kbmod, fn, _Any)
        helpers = types.ModuleType("AgentBot.utils.helpers"); helpers._escape = lambda x: x
        adbmod = types.ModuleType("AgentBot.database")
        for fn in ("get_setting", "set_setting", "get_cards", "get_card", "add_card",
                   "update_card", "delete_card"):
            setattr(adbmod, fn, lambda *a, **k: None)
        for n, m in {"telegram": tg, "telegram.error": terr, "telegram.ext": ext,
                     "telegram.request": req, "Shared.tg_button_styles": tbs,
                     "AgentBot.constants": consts, "AgentBot.handlers.base": base,
                     "AgentBot.keyboards": kbmod, "AgentBot.utils.helpers": helpers,
                     "AgentBot.database": adbmod}.items():
            sys.modules[n] = m
        try:
            # ensure AgentBot package resolves
            apkg = types.ModuleType("AgentBot"); apkg.__path__ = []
            hpkg = types.ModuleType("AgentBot.handlers"); hpkg.__path__ = []
            upkg = types.ModuleType("AgentBot.utils"); upkg.__path__ = []
            sys.modules.setdefault("AgentBot", apkg)
            sys.modules.setdefault("AgentBot.handlers", hpkg)
            sys.modules.setdefault("AgentBot.utils", upkg)
            saved["AgentBot"] = saved.get("AgentBot")
            spec = importlib.util.spec_from_file_location(
                "AgentBot.handlers.settings_payment",
                PROJECT_ROOT / "AgentBot" / "handlers" / "settings_payment.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["AgentBot.handlers.settings_payment"] = mod
            spec.loader.exec_module(mod)

            # The agent payment settings module must not touch the central
            # .env at all: SMS webhook settings live per-agent in the agent
            # database (per-agent webhook isolation). Verify there is no env
            # writer wired up and no dotenv loading in this module.
            self.assertFalse(hasattr(mod, "ENV_FILE"))
            self.assertFalse(hasattr(mod, "_write_env_values"))
            self.assertFalse(hasattr(mod, "_read_env_values"))
            src = (PROJECT_ROOT / "AgentBot" / "handlers" / "settings_payment.py").read_text(encoding="utf-8")
            self.assertNotIn("load_dotenv", src)
        finally:
            sys.modules.pop("AgentBot.handlers.settings_payment", None)
            for n, m in saved.items():
                if m is not None:
                    sys.modules[n] = m
                else:
                    sys.modules.pop(n, None)


class BackupEnvExclusionTests(unittest.TestCase):
    """The bot backup must still NOT include the .env file."""

    def test_bot_backup_file_list_has_no_env(self):
        import ast
        src = (PROJECT_ROOT / "AdminBot" / "userbot.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # find _make_bot_backup_zip and its files_to_add literal
        found_env = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_make_bot_backup_zip":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.List):
                        for elt in sub.elts:
                            frag = ast.get_source_segment(src, elt) or ""
                            if '".env"' in frag or "'\\.env'" in frag or "/ \".env\"" in frag:
                                found_env = True
        self.assertFalse(found_env, ".env must not be part of backup file list")

    def test_backup_zip_writes_no_env_member(self):
        # Build a tiny zip the way the builder does and check no .env member.
        import zipfile as zf
        with tempfile.TemporaryDirectory() as d:
            zpath = Path(d) / "b.zip"
            (Path(d) / "Shared").mkdir()
            (Path(d) / "Shared" / "x.json").write_text("{}", encoding="utf-8")
            with zf.ZipFile(zpath, "w") as z:
                z.write(Path(d) / "Shared" / "x.json", arcname="Shared/x.json")
            with zf.ZipFile(zpath) as z:
                names = z.namelist()
        self.assertNotIn(".env", names)
        self.assertTrue(all(not n.endswith("/.env") for n in names))


class ValidateHelpersTests(unittest.TestCase):
    def test_validate_env_key(self):
        self.assertTrue(validate_env_key("AGENT_BOT_TOKEN"))
        self.assertTrue(validate_env_key("_A"))
        self.assertTrue(validate_env_key("A1_b"))
        self.assertFalse(validate_env_key("1BAD"))
        self.assertFalse(validate_env_key("BAD KEY"))
        self.assertFalse(validate_env_key("KEY-DASH"))
        self.assertFalse(validate_env_key("KEY;DROP"))
        self.assertFalse(validate_env_key(""))

    def test_validate_env_value(self):
        self.assertTrue(validate_env_value("plain"))
        self.assertTrue(validate_env_value("with=equals"))
        self.assertFalse(validate_env_value("line1\nline2"))
        self.assertFalse(validate_env_value("line1\rline2"))
        self.assertFalse(validate_env_value("null\x00byte"))


# ===========================================================================
# Stage-7 remediation: no token may leak through tracebacks in the real
# polling error paths of AgentBot and UserBot.
# ===========================================================================

class PollingTracebackLeakTests(unittest.TestCase):
    """Run the real fatal-error paths and prove the raw exception message
    (which contains a fake token) never reaches the logger output, and
    that no traceback containing the message is emitted."""

    def _capture(self, logger_name):
        import logging
        records = []

        class _Cap(logging.Handler):
            def emit(self, r):
                records.append(r)

        lg = logging.getLogger(logger_name)
        handler = _Cap()
        lg.addHandler(handler)
        old_level = lg.level
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, handler)
        self.addCleanup(lg.setLevel, old_level)
        return records

    def test_agentbot_fatal_polling_error_no_token_no_traceback(self):
        _StubHelper.install(extra_env={})
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "AgentBot.main", PROJECT_ROOT / "AgentBot" / "main.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["AgentBot.main"] = mod
            spec.loader.exec_module(mod)

            class _FakeBuilder:
                def __getattr__(self, name):
                    return lambda *a, **k: self

                def build(self):
                    def _raise(*a, **k):
                        raise RuntimeError(
                            f"Unauthorized: https://api.telegram.org/"
                            f"bot{FAKE_TOKEN}/getMe"
                        )
                    return types.SimpleNamespace(
                        add_handler=lambda *a, **k: None,
                        add_error_handler=lambda *a, **k: None,
                        run_polling=_raise,
                    )

            class _Escape(BaseException):
                pass

            sleeps = []

            def fake_sleep(seconds):
                sleeps.append(seconds)
                raise _Escape("loop escape")

            records = self._capture("AgentBot.main")
            with patch.object(mod, "AGENT_BOT_TOKEN", "dummy"), \
                 patch.object(mod, "Update", types.SimpleNamespace(ALL_TYPES=None)), \
                 patch.object(mod, "init_agent_db", lambda: None), \
                 patch.object(mod, "ApplicationBuilder", _FakeBuilder), \
                 patch.object(mod.time, "sleep", fake_sleep):
                with self.assertRaises(_Escape):
                    mod.main()

            self.assertTrue(records, "expected error logs from the polling path")
            formatted = "\n".join(
                r.getMessage() + ("\n" + r.exc_text if r.exc_text else "")
                for r in records
            )
            self.assertIn("RuntimeError", formatted)
            for forbidden in (FAKE_TOKEN, "AAf4kEXAMPLE", "123456789:"):
                self.assertNotIn(forbidden, formatted,
                                 f"AgentBot polling path leaked {forbidden!r}")
        finally:
            _StubHelper.restore(["AgentBot.main"])

    def test_userbot_polling_crashed_no_token_no_traceback(self):
        _StubHelper.install(extra_env={"USER_BOT_TOKEN": "123456:TEST_DUMMY_TOKEN_FOR_IMPORT_ONLY",
                                       "SUB_SERVER_ENABLED": "0", "ADMIN_ID": "0"})
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "UserBot.main", PROJECT_ROOT / "UserBot" / "main.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["UserBot.main"] = mod
            spec.loader.exec_module(mod)

            crash_count = {"n": 0}

            def _crash(*a, **k):
                crash_count["n"] += 1
                if crash_count["n"] == 1:
                    raise RuntimeError(
                        f"polling died: https://api.telegram.org/"
                        f"bot{FAKE_TOKEN}/getMe"
                    )
                raise _Escape("loop escape")

            class _Escape(BaseException):
                pass

            class _FakeApp:
                def __init__(self):
                    self.bot_data = {}

                def add_handler(self, *a, **k):
                    pass

                def add_error_handler(self, *a, **k):
                    pass

                def run_polling(self, *a, **k):
                    _crash()

            class _FakeBuilder:
                def __getattr__(self, name):
                    return lambda *a, **k: self

                def build(self):
                    return _FakeApp()

            records = self._capture("UserBot.main")
            with patch.object(mod, "_acquire_pid_lock", lambda: True), \
                 patch.object(mod, "_release_pid_lock", lambda: None), \
                 patch.object(mod, "SUB_SERVER_ENABLED", False), \
                 patch.object(mod, "ApplicationBuilder", _FakeBuilder), \
                 patch.object(mod.time, "sleep", lambda s: None):
                with self.assertRaises(_Escape):
                    mod.main()

            self.assertTrue(records, "expected error logs from the polling path")
            formatted = "\n".join(r.getMessage() for r in records)
            self.assertIn("RuntimeError", formatted)
            for forbidden in (FAKE_TOKEN, "AAf4kEXAMPLE", "123456789:"):
                self.assertNotIn(forbidden, formatted,
                                 f"UserBot polling path leaked {forbidden!r}")
        finally:
            _StubHelper.restore(["UserBot.main"])

    def test_agentbot_polling_path_has_no_raw_traceback_logging(self):
        """AST check: the final catch of AgentBot's polling loop must not use
        logger.exception or exc_info=True."""
        import ast
        src = (PROJECT_ROOT / "AgentBot" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # find main() and the final `except Exception` inside its while loop
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.ExceptHandler) and sub.name:
                        handled = ast.unparse(sub.type) if sub.type else ""
                        if "Exception" in handled and "TimedOut" not in handled:
                            for call in ast.walk(sub):
                                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                                    self.assertNotEqual(
                                        call.func.attr, "exception",
                                        "logger.exception is forbidden in fatal polling path",
                                    )
                                if isinstance(call, ast.keyword) and call.arg == "exc_info":
                                    self.fail("exc_info=True is forbidden in fatal polling path")

    def test_userbot_polling_crashed_has_no_exc_info(self):
        import ast
        src = (PROJECT_ROOT / "UserBot" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.name == "e":
                # locate the "Polling crashed" handler
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                        if call.func.attr in {"error", "warning", "exception"}:
                            consts = [ast.unparse(a) for a in call.args if isinstance(a, ast.Constant)]
                            if any("Polling crashed" in c for c in consts):
                                found = True
                                self.assertFalse(
                                    any(kw.arg == "exc_info" for kw in call.keywords),
                                    "exc_info=True is forbidden in Polling crashed path",
                                )
                                self.assertNotEqual(call.func.attr, "exception")
        self.assertTrue(found, "Polling crashed handler not found — check UserBot/main.py")

    def test_customerbot_invalid_token_logs_only_class_name(self):
        """The invalid-token path must log agent id + exception class only:
        no str(e), no traceback, hence no token even in tracebacks."""
        import ast
        src = (PROJECT_ROOT / "CustomerBot" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        checked = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_single_bot":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.ExceptHandler) and sub.type and "Exception" in ast.unparse(sub.type):
                        for call in ast.walk(sub):
                            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                                    and call.func.attr == "error":
                                consts = [ast.unparse(a) for a in call.args if isinstance(a, ast.Constant)]
                                if any("invalid token" in c for c in consts):
                                    checked = True
                                    src_seg = ast.get_source_segment(src, call) or ""
                                    self.assertNotIn("str(e)", src_seg)
                                    self.assertNotIn("exc_info", src_seg)
        self.assertTrue(checked, "invalid-token log call not found in CustomerBot/main.py")


if __name__ == "__main__":
    unittest.main()
