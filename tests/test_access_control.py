"""Access-control regression tests for all four bots (stage 6).

Only real security-relevant functions are executed — no source-text
scanning. Telegram, network, real databases and the real .env are never
touched: telegram modules are stubbed in sys.modules and every module is
restored in tearDownClass.
"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_TELEGRAM_STUBS = ("telegram", "telegram.error", "telegram.ext", "telegram.request")
_OTHER_STUBS: tuple = ()


def _make_stub_modules():
    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return types.SimpleNamespace()

        def __getattr__(self, name):
            return _Any()

    tg = types.ModuleType("telegram")
    tg.__version__ = "20.0"  # satisfies UserBot's version check
    for n in ("Update", "Bot", "BotCommand", "KeyboardButton", "InlineKeyboardMarkup",
              "InlineKeyboardButton", "ReplyKeyboardMarkup", "ReplyKeyboardRemove",
              "InputMediaPhoto", "MenuButtonCommands"):
        setattr(tg, n, _Any)
    tg.Update = type("Update", (), {})
    terr = types.ModuleType("telegram.error")
    for n in ("TelegramError", "NetworkError", "TimedOut", "BadRequest",
              "Forbidden", "Conflict", "RetryAfter"):
        setattr(terr, n, type(n, (Exception,), {}))
    ext = types.ModuleType("telegram.ext")
    for n in ("Application", "ApplicationBuilder", "CommandHandler",
              "MessageHandler", "CallbackQueryHandler"):
        setattr(ext, n, object)
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})

    class _F:
        ALL = TEXT = PHOTO = COMMAND = object()

    ext.filters = _F
    req = types.ModuleType("telegram.request")
    req.HTTPXRequest = object
    return {"telegram": tg, "telegram.error": terr, "telegram.ext": ext, "telegram.request": req}


def _load_module_from_file(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _StubbedModuleTestCase(unittest.TestCase):
    """Base class: stubs telegram + heavy deps, loads the target module from
    its real file, and restores sys.modules exactly in tearDownClass."""

    module_name = ""
    rel_path = ""
    extra_stub_names = _OTHER_STUBS

    @classmethod
    def setUpClass(cls):
        import os
        cls._saved = {n: sys.modules.get(n) for n in _TELEGRAM_STUBS + cls.extra_stub_names}
        cls._installed = []
        cls._installed.extend(_make_stub_modules().items())
        # UserBot.main validates token presence at import time; provide a
        # syntactically valid DUMMY token (never a real credential).
        cls._env_patcher = patch.dict(os.environ, {
            "USER_BOT_TOKEN": "123456:TEST_DUMMY_TOKEN_FOR_IMPORT_ONLY",
            "ADMIN_ID": "0",
        }, clear=False)
        cls._env_patcher.start()
        for n, m in cls._installed:
            if sys.modules.get(n) is not m:
                sys.modules[n] = m
        cls.mod = _load_module_from_file(cls.module_name, cls.rel_path)

    @classmethod
    def tearDownClass(cls):
        cls._env_patcher.stop()
        sys.modules.pop(cls.module_name, None)
        for n, _m in cls._installed:
            sys.modules.pop(n, None)
        for n, m in cls._saved.items():
            if m is not None:
                sys.modules[n] = m
        # Transitive bot modules imported during this class must stay in
        # sys.modules: later test files import the same modules, and deleting
        # them here breaks those imports. They are real modules from the
        # project — safe to keep globally.


class _FakeUser:
    def __init__(self, uid=777):
        self.id = uid


class _FakeAnswer:
    def __init__(self):
        self.calls = []

    async def __call__(self, text=None, show_alert=False, **kw):
        self.calls.append((text, show_alert))


class _FakeCallbackQuery:
    def __init__(self, data="forcejoin:check"):
        self.data = data
        self.answer = _FakeAnswer()
        self.message = None


class _FakeMessage:
    def __init__(self, text="hello"):
        self.text = text
        self.replies = []

    async def reply_text(self, text, *a, **kw):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, callback_query=None, message=None, user=None):
        self.callback_query = callback_query
        self.message = message
        self.effective_user = user
        self.effective_message = message


class _FakeBot:
    def __init__(self, member_status="member", raise_error=False):
        self.member_status = member_status
        self.raise_error = raise_error
        self.calls = []

    async def get_chat_member(self, chat_target, user_id):
        self.calls.append((chat_target, user_id))
        if self.raise_error:
            raise RuntimeError("getChatMember failed")
        return types.SimpleNamespace(status=self.member_status)


def _context(bot_data=None, bot=None):
    return types.SimpleNamespace(bot_data=bot_data or {}, bot=bot or _FakeBot())


def _assert_stopped(coro):
    """Run the coroutine and assert ApplicationHandlerStop was raised."""
    AHS = sys.modules["telegram.ext"].ApplicationHandlerStop
    try:
        asyncio.run(coro)
    except AHS:
        return
    raise AssertionError("ApplicationHandlerStop was not raised")


def _assert_passed(coro):
    AHS = sys.modules["telegram.ext"].ApplicationHandlerStop
    try:
        asyncio.run(coro)
    except AHS:
        raise AssertionError("middleware unexpectedly raised ApplicationHandlerStop")


# ===========================================================================
# CustomerBot
# ===========================================================================

class CustomerBotAccessTests(_StubbedModuleTestCase):
    module_name = "CustomerBot.main"
    rel_path = "CustomerBot/main.py"
    extra_stub_names = ()

    def setUp(self):
        self.mod.get_force_join_settings = lambda agent_id: {
            "enabled": 0, "channel_username": ""
        }

    def test_banned_forcejoin_check_stopped(self):
        """The core bug: banned user must NOT slip through forcejoin:check."""
        with patch.object(self.mod, "get_user", side_effect=None, create=True):
            with patch.object(
                self.mod, "get_user",
                lambda agent_id, tid: {"is_banned": 1},
            ):
                upd = _FakeUpdate(callback_query=_FakeCallbackQuery("forcejoin:check"), user=_FakeUser(7))
                _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_banned_normal_callback_stopped(self):
        with patch.object(self.mod, "get_user", lambda a, t: {"is_banned": 1}):
            upd = _FakeUpdate(callback_query=_FakeCallbackQuery("buy:confirm"), user=_FakeUser(7))
            _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_banned_message_stopped(self):
        with patch.object(self.mod, "get_user", lambda a, t: {"is_banned": 1}):
            upd = _FakeUpdate(message=_FakeMessage("خرید"), user=_FakeUser(7))
            _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_banned_command_stopped(self):
        with patch.object(self.mod, "get_user", lambda a, t: {"is_banned": 1}):
            upd = _FakeUpdate(message=_FakeMessage("/start"), user=_FakeUser(7))
            _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_db_error_fails_closed(self):
        """A ban-check database error must stop the user (fail closed)."""
        def boom(agent_id, telegram_id):
            raise RuntimeError("db unavailable")
        with patch.object(self.mod, "get_user", boom):
            upd = _FakeUpdate(message=_FakeMessage("hello"), user=_FakeUser(7))
            _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_healthy_user_forcejoin_check_passes(self):
        """Healthy users must still reach callback_handler for forcejoin:check."""
        with patch.object(self.mod, "get_user", lambda a, t: {"is_banned": 0}):
            upd = _FakeUpdate(callback_query=_FakeCallbackQuery("forcejoin:check"), user=_FakeUser(7))
            _assert_passed(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))

    def test_healthy_member_passes(self):
        with patch.object(self.mod, "get_user", lambda a, t: None):
            with patch.object(
                self.mod, "get_force_join_settings",
                lambda a: {"enabled": 1, "channel_username": "@chan"},
            ):
                with patch.object(self.mod, "force_join_keyboard", lambda link: object()):
                    bot = _FakeBot(member_status="member")
                    upd = _FakeUpdate(message=_FakeMessage("خرید"), user=_FakeUser(7))
                    _assert_passed(self.mod.force_join_middleware(upd, _context({"agent_id": 42}, bot)))
                    self.assertEqual(len(bot.calls), 1)

    def test_healthy_non_member_stopped(self):
        with patch.object(self.mod, "get_user", lambda a, t: None):
            with patch.object(
                self.mod, "get_force_join_settings",
                lambda a: {"enabled": 1, "channel_username": "@chan"},
            ):
                with patch.object(self.mod, "force_join_keyboard", lambda link: object()):
                    bot = _FakeBot(member_status="left")
                    upd = _FakeUpdate(message=_FakeMessage("خرید"), user=_FakeUser(7))
                    _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42}, bot)))

    def test_missing_agent_id_stops(self):
        upd = _FakeUpdate(message=_FakeMessage("hello"), user=_FakeUser(7))
        _assert_stopped(self.mod.force_join_middleware(upd, _context({})))

    def test_missing_effective_user_stops(self):
        upd = _FakeUpdate(message=_FakeMessage("hello"), user=None)
        _assert_stopped(self.mod.force_join_middleware(upd, _context({"agent_id": 42})))


# ===========================================================================
# UserBot
# ===========================================================================

class UserBotAccessTests(_StubbedModuleTestCase):
    module_name = "UserBot.main"
    rel_path = "UserBot/main.py"

    def _userbot_db_stub(self, row=None, exc=None):
        import logging
        mod = types.ModuleType("UserBot._userbot_db_stub")
        self._saved_udb = self.mod.userbot_db

        class _UDB:
            @staticmethod
            def get_user_by_telegram_id(tid):
                if exc:
                    raise exc
                return row

        self.mod.userbot_db = _UDB()
        self.addCleanup(setattr, self.mod, "userbot_db", self._saved_udb)

    def test_banned_message_stopped(self):
        self._userbot_db_stub(row={"is_banned": 1})
        upd = _FakeUpdate(message=_FakeMessage("hi"), user=_FakeUser(7))
        _assert_stopped(self.mod._userbot_ban_middleware(upd, _context()))

    def test_banned_callback_stopped(self):
        self._userbot_db_stub(row={"is_banned": 1})
        upd = _FakeUpdate(callback_query=_FakeCallbackQuery("menu:main"), user=_FakeUser(7))
        _assert_stopped(self.mod._userbot_ban_middleware(upd, _context()))

    def test_db_error_stopped(self):
        self._userbot_db_stub(exc=RuntimeError("db down"))
        upd = _FakeUpdate(message=_FakeMessage("hi"), user=_FakeUser(7))
        _assert_stopped(self.mod._userbot_ban_middleware(upd, _context()))

    def test_healthy_user_passes(self):
        self._userbot_db_stub(row={"is_banned": 0})
        upd = _FakeUpdate(message=_FakeMessage("hi"), user=_FakeUser(7))
        _assert_passed(self.mod._userbot_ban_middleware(upd, _context()))

    def test_ban_middlewares_registered_in_group_minus_one(self):
        """Both ban middlewares must be registered in group -1 before main
        handlers — verified against the real registration function."""
        registered = []

        class _AnyFilter:
            def __getattr__(self, name):
                return object()

            def __call__(self, *a, **k):
                return object()

            def __or__(self, other):
                return self

            def __and__(self, other):
                return self

            def __invert__(self):
                return self

        class _F:
            def __or__(self, other):
                return self

            def __and__(self, other):
                return self

            def __invert__(self):
                return self

            def __ror__(self, other):
                return self

            def __rand__(self, other):
                return self

        class _FakeApp:
            bot_data = {}

            def add_handler(self, handler, group=0):
                registered.append((type(handler).__name__, getattr(handler, "callback", None), group))

            def add_error_handler(self, handler):
                pass

        import types as _t
        self.mod.CommandHandler = lambda n, cb: _t.SimpleNamespace(callback=cb)
        self.mod.CallbackQueryHandler = lambda cb, **kw: _t.SimpleNamespace(callback=cb)
        self.mod.MessageHandler = lambda f, cb: _t.SimpleNamespace(callback=cb)
        self.mod.filters = _t.SimpleNamespace(
            ALL=_F(), TEXT=_F(), PHOTO=_F(), COMMAND=_F(),
            Regex=lambda pattern, **kw: _F(),
        )
        self.mod._attach_userbot_handlers(_FakeApp())

        ban_cbs = [(n, cb, g) for (n, cb, g) in registered
                   if getattr(cb, "__name__", "") == "_userbot_ban_middleware"]
        self.assertEqual(len(ban_cbs), 2, "MessageHandler + CallbackQueryHandler expected")
        self.assertTrue(all(g == -1 for _, _, g in ban_cbs))
        self.assertLess([i for i, r in enumerate(registered) if r[2] == -1][-1],
                        [i for i, r in enumerate(registered) if r[2] != -1][0],
                        "group -1 middleware must be registered before all main handlers")


# ===========================================================================
# AdminBot
# ===========================================================================

class AdminBotAccessTests(_StubbedModuleTestCase):
    module_name = "AdminBot.servers"
    rel_path = "AdminBot/servers.py"

    def setUp(self):
        import os
        self._os_patcher = patch.dict(os.environ, {"ADMIN_ID": "111"}, clear=False)
        self._os_patcher.start()
        self.addCleanup(self._os_patcher.stop)

    def test_valid_admin_accepted(self):
        self.assertTrue(self.mod._is_authorized_admin(_FakeUpdate(user=_FakeUser(111))))

    def test_other_user_rejected(self):
        self.assertFalse(self.mod._is_authorized_admin(_FakeUpdate(user=_FakeUser(222))))

    def test_missing_user_rejected(self):
        self.assertFalse(self.mod._is_authorized_admin(_FakeUpdate(user=None)))

    def _with_env(self, value):
        import os
        with patch.dict(os.environ, {"ADMIN_ID": value}, clear=False):
            return self.mod._is_authorized_admin(_FakeUpdate(user=_FakeUser(111)))

    def test_admin_id_zero_rejected(self):
        self.assertFalse(self._with_env("0"))

    def test_admin_id_empty_rejected(self):
        self.assertFalse(self._with_env(""))

    def test_admin_id_invalid_rejected(self):
        self.assertFalse(self._with_env("abc"))

    def test_generic_text_path_authorized_check(self):
        """handle_admin_menu must ignore updates from non-admins."""
        with patch.object(self.mod, "_is_authorized_admin", return_value=False) as chk:
            sent = []
            upd = _FakeUpdate(message=_FakeMessage("any text"), user=_FakeUser(999))
            upd.message.reply_text = lambda *a, **k: sent.append(a)
            asyncio.run(self.mod.handle_admin_menu(upd, _context()))
            chk.assert_called_once()
            self.assertEqual(sent, [])

    def test_generic_callback_path_authorized_check(self):
        with patch.object(self.mod, "_is_authorized_admin", return_value=False) as chk:
            upd = _FakeUpdate(callback_query=_FakeCallbackQuery("menu:main"), user=_FakeUser(999))
            asyncio.run(self.mod.admin_inline_handler(upd, _context()))
            chk.assert_called_once()

    def test_main_admin_id_fail_closed(self):
        """The env-int helper used by AdminBot/main must parse a bad ADMIN_ID
        to 0 (fail closed), not raise or fall back to another value."""
        import os
        from Shared.env_utils import env_int
        with patch.dict(os.environ):
            os.environ.pop("ADMIN_ID", None)
            with patch.dict(os.environ, {"ADMIN_ID": "abc"}):
                self.assertEqual(env_int("ADMIN_ID", 0, minimum=0), 0)
            with patch.dict(os.environ, {"ADMIN_ID": ""}):
                self.assertEqual(env_int("ADMIN_ID", 0, minimum=0), 0)
            self.assertEqual(env_int("ADMIN_ID", 0, minimum=0), 0)  # missing -> default 0


# ===========================================================================
# AgentBot
# ===========================================================================

class AgentBotAccessTests(_StubbedModuleTestCase):
    module_name = "AgentBot.handlers.base"
    rel_path = "AgentBot/handlers/base.py"
    extra_stub_names = ()

    def setUp(self):
        from AgentBot.constants import UD_AGENT_ID, UD_AGENT_DATA
        self.UD_AGENT_ID = UD_AGENT_ID
        self.UD_AGENT_DATA = UD_AGENT_DATA

    def _ctx(self):
        return types.SimpleNamespace(user_data={}, effective_user=None)

    def test_active_agent_authenticated(self):
        agent_row = {"id": 5, "is_active": 1, "telegram_id": 7}
        with patch.object(self.mod.agent_db, "get_agent_by_telegram_id", lambda tid: agent_row):
            upd = _FakeUpdate(user=_FakeUser(7))
            ctx = self._ctx()
            result = asyncio.run(self.mod.authenticate(upd, ctx))
            self.assertEqual(result, agent_row)
            self.assertEqual(ctx.user_data[self.UD_AGENT_ID], 5)

    def test_inactive_agent_rejected(self):
        with patch.object(self.mod.agent_db, "get_agent_by_telegram_id",
                          lambda tid: {"id": 5, "is_active": 0}):
            upd = _FakeUpdate(user=_FakeUser(7))
            result = asyncio.run(self.mod.authenticate(upd, self._ctx()))
            self.assertIsNone(result)

    def test_deleted_agent_rejected(self):
        with patch.object(self.mod.agent_db, "get_agent_by_telegram_id", lambda tid: None):
            upd = _FakeUpdate(user=_FakeUser(7))
            result = asyncio.run(self.mod.authenticate(upd, self._ctx()))
            self.assertIsNone(result)

    def test_stale_context_does_not_authenticate_inactive_agent(self):
        """The agent must be re-fetched from DB on every update; a stale
        user_data entry must not bypass deactivation."""
        with patch.object(self.mod.agent_db, "get_agent_by_telegram_id",
                          lambda tid: {"id": 5, "is_active": 0}):
            upd = _FakeUpdate(user=_FakeUser(7))
            ctx = self._ctx()
            ctx.user_data[self.UD_AGENT_ID] = 5  # stale cached id
            ctx.user_data[self.UD_AGENT_DATA] = {"id": 5, "is_active": 1}
            result = asyncio.run(self.mod.authenticate(upd, ctx))
            self.assertIsNone(result)

    def test_agentbot_text_handler_uses_fresh_auth(self):
        """handle_agent_text must call authenticate (DB re-check) each time."""
        source_mod = _load_module_from_file("AgentBot.handlers.main_menu", "AgentBot/handlers/main_menu.py")
        try:
            calls = []

            async def fake_auth(update, context):
                calls.append(1)
                return None

            msg = _FakeMessage("hello")
            upd = _FakeUpdate(message=msg, user=_FakeUser(7))

            with patch.object(source_mod, "authenticate", fake_auth):
                with patch.object(source_mod, "clear_state", lambda ctx: None):
                    asyncio.run(source_mod.handle_agent_text(upd, self._ctx()))
            self.assertEqual(calls, [1])
        finally:
            sys.modules.pop("AgentBot.handlers.main_menu", None)

    def _ctx(self):  # used by the last test as well
        return types.SimpleNamespace(user_data={}, effective_user=None)


if __name__ == "__main__":
    unittest.main()
