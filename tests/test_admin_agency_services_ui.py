"""Tests for the AdminBot agency-services UI (list / search / filter / sort /
detail / finance / ownership / admin-guard).

Only stdlib + project utilities are used; agency.db is redirected to a temp
directory and telegram modules are stubbed. No real .env, token, database,
panel or Telegram traffic is ever touched.
"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Shared import agent_db
from Shared import database as shared_database

ADMIN_ID = 111
OTHER_ID = 222


# ---------------------------------------------------------------------------
#   telegram stubs (module import needs them)
# ---------------------------------------------------------------------------

class _Any:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self


class _FakeInlineButton:
    def __init__(self, text="", callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data


class _FakeInlineKeyboardMarkup:
    def __init__(self, inline_keyboard=None, **kwargs):
        self.inline_keyboard = inline_keyboard or []


def _install_telegram_stubs():
    tg = types.ModuleType("telegram")
    tg.__version__ = "20.0"
    for n in ("Update", "Bot", "BotCommand", "ReplyKeyboardMarkup", "KeyboardButton",
              "ReplyKeyboardRemove", "InputMediaPhoto"):
        setattr(tg, n, _Any)
    tg.InlineKeyboardButton = _FakeInlineButton
    tg.InlineKeyboardMarkup = _FakeInlineKeyboardMarkup
    terr = types.ModuleType("telegram.error")
    for n in ("TelegramError", "BadRequest", "Forbidden", "NetworkError", "TimedOut"):
        setattr(terr, n, type(n, (Exception,), {}))
    ext = types.ModuleType("telegram.ext")
    ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    ext.ApplicationHandlerStop = type("ApplicationHandlerStop", (Exception,), {})
    for n in ("Application", "ApplicationBuilder", "CommandHandler",
              "MessageHandler", "CallbackQueryHandler"):
        setattr(ext, n, _Any)
    ext.filters = types.SimpleNamespace(ALL=_Any(), TEXT=_Any(), PHOTO=_Any(),
                                        COMMAND=_Any(), Regex=lambda *a, **k: _Any())
    req = types.ModuleType("telegram.request")
    req.HTTPXRequest = _Any
    tbs = types.ModuleType("Shared.tg_button_styles")
    tbs.inline_button = lambda *a, **k: _FakeInlineButton(*a, **k)
    tbs.keyboard_button = lambda *a, **k: _Any(*a, **k)
    saved = {n: sys.modules.get(n) for n in
             ("telegram", "telegram.error", "telegram.ext", "telegram.request",
              "Shared.tg_button_styles", "AdminBot", "AdminBot.keyboards",
              "AdminBot.agencies", "AgentBot", "AgentBot.database",
              "CustomerBot", "CustomerBot.database")}
    sys.modules.update({
        "telegram": tg, "telegram.error": terr, "telegram.ext": ext,
        "telegram.request": req, "Shared.tg_button_styles": tbs,
    })
    return saved


def _restore_modules(saved):
    for n, m in saved.items():
        if m is not None:
            sys.modules[n] = m
        else:
            sys.modules.pop(n, None)


def _stub_sibling_dbs():
    """AgentBot/CustomerBot database modules are imported by agencies.py; stub
    them so the real agent_bot.db / customer_bot.db files are never touched."""
    for name in ("AgentBot.database", "CustomerBot.database"):
        mod = types.ModuleType(name)

        def _mk_getattr():
            def _getattr(attr):
                def _fn(*a, **k):
                    return {}
                return _fn
            return _getattr

        mod.__getattr__ = _mk_getattr()
        sys.modules[name] = mod

    kb = types.ModuleType("AdminBot.keyboards")
    kb.admin_main_keyboard = lambda: None
    kb.cancel_keyboard = lambda: None
    pkg = types.ModuleType("AdminBot")
    pkg.__path__ = []
    sys.modules["AdminBot"] = pkg
    sys.modules["AdminBot.keyboards"] = kb


_SAVED_MODULES = {}


def _load_agencies_module():
    global _SAVED_MODULES
    _SAVED_MODULES = _install_telegram_stubs()
    _stub_sibling_dbs()
    spec = importlib.util.spec_from_file_location(
        "AdminBot.agencies", PROJECT_ROOT / "AdminBot" / "agencies.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["AdminBot.agencies"] = mod
    spec.loader.exec_module(mod)
    return mod


def _unload_agencies_module():
    _restore_modules(_SAVED_MODULES)


# ---------------------------------------------------------------------------
#   update / context fakes
# ---------------------------------------------------------------------------

def _mk_update(callback_data=None, text=None, user_id=ADMIN_ID):
    upd = MagicMock()
    upd.effective_user.id = user_id
    if callback_data is not None:
        q = AsyncMock()
        q.data = callback_data
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.message = SimpleMessage()
        upd.callback_query = q
        upd.message = None
    else:
        upd.callback_query = None
        msg = AsyncMock()
        msg.text = text
        msg.reply_text = AsyncMock()
        upd.message = msg
    return upd


class SimpleMessage:
    def __init__(self):
        self.chat_id = 1


def _mk_context(user_data=None):
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    return ctx


def _rendered_text(upd):
    call = upd.callback_query.edit_message_text.await_args
    if call is None:
        return ""
    if call.args:
        return call.args[0]
    return call.kwargs.get("text", "")


def _run(coro):
    # event loop مستقل برای هر تست؛ چون برخی تست‌های پروژه (مثل
    # test_access_control) با asyncio.run حلقه سراسری را می‌بندند.
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
#   base fixture
# ---------------------------------------------------------------------------

# همه fixtureهای زمانی UTC ساخته می‌شوند (قرارداد ذخیره‌سازی end_date در DB)
from datetime import timezone

def _utcnow_fixed():
    return datetime.now(timezone.utc).replace(tzinfo=None)

now = _utcnow_fixed()
FMT = "%Y-%m-%d %H:%M:%S"


class _Base(unittest.TestCase):
    mod = None

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_agencies_module()

    @classmethod
    def tearDownClass(cls):
        _unload_agencies_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "agency.db"
        p = patch.object(agent_db, "DB_PATH", self.db_path)
        p.start()
        self.addCleanup(p.stop)
        self._reset_db_flag()
        self.addCleanup(self._reset_db_flag)
        # در اجرای واقعی ADMIN_ID از .env لود می‌شود؛ اینجا برای مسیرهای
        # روتینگ شبیه‌سازی می‌شود (مقدار آزمایشی).
        env_p = patch.dict(os.environ, {"ADMIN_ID": str(ADMIN_ID)})
        env_p.start()
        self.addCleanup(env_p.stop)

        self.agent1 = agent_db.upsert_agent(1001, username="agent_one", full_name="علی اول")
        self.agent2 = agent_db.upsert_agent(1002, username="agent_two", full_name="نازی دوم")
        self.svc_active12 = self._mk_service(self.agent1, "علی مشتری", days=12, active=1)
        self.svc_near1 = self._mk_service(self.agent1, "نازی مشتری", days=1, active=1)
        self.svc_inactive = self._mk_service(self.agent1, "رضا مشتری", days=30, active=0)
        self.svc_expired = self._mk_service(self.agent1, "مریم مشتری", days=-5, active=1)
        self.svc_other_agent = self._mk_service(self.agent2, "سرویس دیگر", days=20, active=1)

    def _reset_db_flag(self):
        agent_db._db_initialized = False
        agent_db._init_db_path = ""

    def _mk_service(self, agent_id, name, days, active, gb=10.0):
        svc = agent_db.create_service(
            agent_id=agent_id,
            customer_id=agent_db.upsert_customer(agent_id, 9000 + agent_id, full_name=name),
            server_id=1,
            server_title="🇩🇪 آلمان",
            name=name,
            panel_user_uuid=f"uuid-{agent_id}-{name}",
            usage_limit=gb,
            days=max(0, days),
        )
        end = (now + timedelta(days=days)).strftime(FMT) if days else ""
        agent_db.update_service(int(svc["id"]), {"is_active": active, "end_date": end})
        return int(svc["id"])


# ---------------------------------------------------------------------------
#   DB layer: filter / sort / search / pagination
# ---------------------------------------------------------------------------

class ListSortedFilterSearchTests(_Base):

    def test_empty_result(self):
        rows, total = agent_db.list_services_by_agent_sorted(9999)
        self.assertEqual((rows, total), ([], 0))

    def test_pagination_seven_items_page_size_six(self):
        for i in range(7):
            agent_db.create_service(
                agent_id=self.agent1, customer_id=1, server_id=1,
                server_title="s", name=f"p{i}", panel_user_uuid=f"u-p{i}",
                usage_limit=5, days=10,
            )
        page1, total = agent_db.list_services_by_agent_sorted(self.agent1, page=1, page_size=6)
        page2, _ = agent_db.list_services_by_agent_sorted(self.agent1, page=2, page_size=6)
        self.assertEqual(total, 11)  # 4 base + 7 new
        self.assertEqual(len(page1), 6)
        self.assertEqual(len(page2), 5)

    def test_single_item(self):
        a = agent_db.upsert_agent(777, username="solo")
        agent_db.create_service(agent_id=a, customer_id=1, server_id=1,
                                name="تنها", panel_user_uuid="solo-1")
        rows, total = agent_db.list_services_by_agent_sorted(a)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["name"], "تنها")

    def test_filter_active_excludes_expired(self):
        # منطق انحصاری: منقضی (حتی اگر is_active=1 باشد) در فیلتر فعال نمی‌آید
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="active")
        self.assertEqual({r["name"] for r in rows}, {"علی مشتری", "نازی مشتری"})

    def test_inactive_and_expired_filters_do_not_overlap(self):
        inactive, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="inactive")
        expired, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        # غیرفعال‌کردن دستی معنای منقضی ندارد و برعکس
        self.assertEqual([r["name"] for r in inactive], ["رضا مشتری"])
        self.assertEqual([r["name"] for r in expired], ["مریم مشتری"])
        self.assertFalse({r["name"] for r in inactive} & {r["name"] for r in expired})

    def test_filter_inactive_is_not_confused_with_expired(self):
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="inactive")
        self.assertEqual([r["name"] for r in rows], ["رضا مشتری"])

    def test_filter_expired_only_time_expired(self):
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        self.assertEqual([r["name"] for r in rows], ["مریم مشتری"])

    def test_sort_expiry_puts_unknown_end_last(self):
        svc = agent_db.create_service(
            agent_id=self.agent1, customer_id=1, server_id=1,
            server_title="s", name="بدون تاریخ", panel_user_uuid="nodate",
            usage_limit=5, days=2,
        )
        agent_db.update_service(int(svc["id"]), {"end_date": ""})
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, sort="expiry")
        # سرویس بدون تاریخ انقضا باید در انتهای «نزدیک‌ترین انقضا» بیاید
        self.assertEqual(rows[-1]["name"], "بدون تاریخ")
        ends = [r["end_date"] for r in rows if r["end_date"]]
        self.assertEqual(ends, sorted(ends))

    def test_sort_name(self):
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, sort="name")
        names = [r["name"] for r in rows]
        self.assertEqual(names, sorted(names))

    def test_search_by_name_code_and_uuid(self):
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, search="علی مشتری")
        self.assertEqual(len(rows), 1)
        code = agent_db._service_code_from_comment(rows[0]["comment"])
        by_code, _ = agent_db.list_services_by_agent_sorted(self.agent1, search=code)
        self.assertEqual(len(by_code), 1)
        by_uuid, _ = agent_db.list_services_by_agent_sorted(self.agent1, search="uuid-")
        self.assertGreaterEqual(len(by_uuid), 1)

    def test_search_scoped_to_agent(self):
        rows1, t1 = agent_db.list_services_by_agent_sorted(self.agent1, search="سرویس دیگر")
        rows2, t2 = agent_db.list_services_by_agent_sorted(self.agent2, search="سرویس دیگر")
        self.assertEqual((t1, rows1), (0, []))
        self.assertEqual(t2, 1)

    def test_deleted_never_returned(self):
        svc = agent_db.create_service(
            agent_id=self.agent1, customer_id=1, server_id=1,
            name="حذف‌شدنی", panel_user_uuid="gone",
        )
        agent_db.delete_service(int(svc["id"]))
        rows, total = agent_db.list_services_by_agent_sorted(self.agent1)
        self.assertFalse(any(r["id"] == svc["id"] for r in rows))
        self.assertEqual(total, 4)  # unchanged

    def test_service_transactions_only_linked(self):
        agent_db.add_transaction(self.agent1, 100, "purchase", "خرید A", service_id=self.svc_active12)
        agent_db.add_transaction(self.agent1, 500, "charge", "شارژ کلی نماینده")  # service_id=0
        linked = agent_db.get_service_transactions(self.svc_active12, self.agent1)
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["description"], "خرید A")


# ---------------------------------------------------------------------------
#   UI helpers: labels / status / state isolation
# ---------------------------------------------------------------------------

class UIHelperTests(_Base):

    def test_ui_state_isolated_per_agent_and_admin(self):
        ctx = _mk_context()
        s1 = self.mod._svc_ui(ctx, self.agent1)
        s1["filter"] = "active"
        s1["query"] = "abc"
        s1["page"] = 3
        s2 = self.mod._svc_ui(ctx, self.agent2)
        self.assertEqual((s2["filter"], s2["query"], s2["page"]), ("all", "", 1))
        ctx2 = _mk_context()
        s1b = self.mod._svc_ui(ctx2, self.agent1)
        self.assertEqual((s1b["filter"], s1b["query"], s1b["page"]), ("all", "", 1))

    def test_button_label_truncates_long_names_and_keeps_id(self):
        label = self.mod._svc_button_label({
            "id": 66, "name": "ن" * 60, "is_active": 1,
            "end_date": (now + timedelta(days=12)).strftime(FMT),
        })
        self.assertIn("#66", label)
        self.assertIn("…", label)
        self.assertLessEqual(len(label), 48)

    def test_expiry_parts_statuses(self):
        p_active = self.mod._svc_expiry_parts({"is_active": 1, "end_date": (now + timedelta(days=12, hours=2)).strftime(FMT)})
        self.assertEqual(p_active["icon"], "🟢")
        self.assertEqual(p_active["time"], "12 روز باقی‌مانده")
        p_near = self.mod._svc_expiry_parts({"is_active": 1, "end_date": (now + timedelta(days=1, hours=2)).strftime(FMT)})
        self.assertEqual(p_near["icon"], "⏳")
        self.assertTrue(p_near["near"])
        p_tomorrow = self.mod._svc_expiry_parts({"is_active": 1, "end_date": (now + timedelta(days=1, hours=3)).strftime(FMT)})
        self.assertEqual(p_tomorrow["time"], "فردا منقضی می‌شود")
        p_expired = self.mod._svc_expiry_parts({"is_active": 1, "end_date": (now - timedelta(days=2)).strftime(FMT)})
        self.assertEqual(p_expired["icon"], "🔴")
        self.assertTrue(p_expired["expired"])
        p_inactive = self.mod._svc_expiry_parts({"is_active": 0, "end_date": (now + timedelta(days=2)).strftime(FMT)})
        self.assertEqual(p_inactive["icon"], "⚪️")
        p_unknown = self.mod._svc_expiry_parts({"is_active": 1, "end_date": "", "days_left": None})
        self.assertTrue(p_unknown["unknown_time"])
        self.assertEqual(p_unknown["time"], "نامشخص")

    def test_status_word_distinguishes_inactive_vs_expired(self):
        # تعریف انحصاری: منقضی مستقل از is_active؛ غیرفعال فقط وقتی منقضی نیست
        self.assertEqual(
            self.mod._svc_status_word({"is_active": 0, "end_date": (now + timedelta(days=5)).strftime(FMT)}),
            "غیرفعال")
        self.assertEqual(
            self.mod._svc_status_word({"is_active": 1, "end_date": (now - timedelta(days=1)).strftime(FMT)}),
            "منقضی شده")
        self.assertEqual(
            self.mod._svc_status_word({"is_active": 0, "end_date": (now - timedelta(days=1)).strftime(FMT)}),
            "منقضی شده")

    def test_detail_text_escapes_html(self):
        ctx = _mk_context()
        text = self.mod._service_detail_text(
            {
                "id": 1, "agent_id": self.agent1, "name": '<b>"تزریق"</b> & <script>',
                "comment": "code:1234567|note:<i>x</i>",
                "server_title": "<srv&>", "usage_current": 1, "usage_limit": 10,
                "end_date": (now + timedelta(days=3)).strftime(FMT),
                "is_active": 1,
            },
            {"full_name": "<agent&>", "username": ""},
            ctx, self.agent1,
        )
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;agent&amp;&gt;", text)

    def test_note_edit_rebuild_comment_keeps_code(self):
        rebuilt = self.mod._svc_rebuild_comment("ali|0421531|code:1234567|note:old", "new")
        self.assertIn("code:1234567", rebuilt)
        self.assertIn("note:new", rebuilt)
        self.assertNotIn("old", rebuilt)


class ServiceNameNoteWizardTests(_Base):
    def _context(self):
        return _mk_context({
            "state": self.mod.AGENCY_SVC_EDITNOTE,
            self.mod.AGENCY_SVC_EDIT_TARGET: {
                "agent_id": self.agent1,
                "service_id": self.svc_active12,
            },
        })

    def test_name_and_note_can_be_sent_as_two_messages(self):
        ctx = self._context()
        name_update = _mk_update(text="علی جدید")
        with patch.object(self.mod, "_save_service_name_note", new=AsyncMock()) as save:
            consumed = _run(self.mod.handle_service_note_edit_text(name_update, ctx))
        self.assertTrue(consumed)
        self.assertEqual(ctx.user_data["state"], self.mod.AGENCY_SVC_EDITNOTE_VALUE)
        self.assertEqual(
            ctx.user_data[self.mod.AGENCY_SVC_EDIT_TARGET]["new_name"],
            "علی جدید",
        )
        save.assert_not_awaited()
        self.assertIn("مرحله ۲ از ۲", name_update.message.reply_text.await_args.args[0])

        note_update = _mk_update(text="کانکشن پر سرعت")
        with patch.object(self.mod, "_save_service_name_note", new=AsyncMock()) as save:
            consumed = _run(self.mod.handle_service_note_edit_text(note_update, ctx))
        self.assertTrue(consumed)
        save.assert_awaited_once()
        args = save.await_args.args
        self.assertEqual(args[-2:], ("علی جدید", "کانکشن پر سرعت"))

    def test_legacy_name_pipe_note_format_still_works(self):
        ctx = self._context()
        update = _mk_update(text="علی جدید | یادداشت جدید")
        with patch.object(self.mod, "_save_service_name_note", new=AsyncMock()) as save:
            consumed = _run(self.mod.handle_service_note_edit_text(update, ctx))
        self.assertTrue(consumed)
        save.assert_awaited_once()
        self.assertEqual(save.await_args.args[-2:], ("علی جدید", "یادداشت جدید"))

    def test_invalid_name_keeps_first_step_active(self):
        ctx = self._context()
        update = _mk_update(text="ع")
        with patch.object(self.mod, "_save_service_name_note", new=AsyncMock()) as save:
            consumed = _run(self.mod.handle_service_note_edit_text(update, ctx))
        self.assertTrue(consumed)
        self.assertEqual(ctx.user_data["state"], self.mod.AGENCY_SVC_EDITNOTE)
        save.assert_not_awaited()


# ---------------------------------------------------------------------------
#   Callback router: guard, navigation, ownership, stale callbacks
# ---------------------------------------------------------------------------

class CallbackRouterTests(_Base):

    def test_non_admin_rejected(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:services:{self.agent1}:1", user_id=OTHER_ID)
            await self.mod.handle_agencies_callback(upd, ctx)
            args = upd.callback_query.answer.await_args
            self.assertIn("دسترسی", str(args))
        _run(flow())

    def test_list_shows_real_header_and_full_width_buttons(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:services:{self.agent1}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("مدیریت اشتراک‌های نماینده", text)
            self.assertIn("علی اول", text)
            self.assertIn("تعداد کل اشتراک‌ها: <b>4</b>", text)
            kb = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            full_width = [r for r in kb.inline_keyboard
                          if len(r) == 1 and "· #" in r[0].text]
            self.assertGreaterEqual(len(full_width), 1)
            labels = [r[0].text for r in full_width]
            self.assertTrue(any("#%d" % self.svc_active12 in t for t in labels), labels)
        _run(flow())

    def test_empty_list_message(self):
        async def flow():
            a = agent_db.upsert_agent(555, username="empty_agent")
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:services:{a}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("هیچ اشتراکی ثبت نشده است", text)
        _run(flow())

    def test_empty_search_message(self):
        async def flow():
            ctx = _mk_context()
            state = self.mod._svc_ui(ctx, self.agent1)
            state["query"] = "چیزی-که-نیست"
            upd = _mk_update(callback_data=f"agency:services:{self.agent1}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("موردی مطابق جستجو پیدا نشد", text)
        _run(flow())

    def test_detail_then_back_preserves_filter(self):
        async def flow():
            ctx = _mk_context()
            await self.mod.handle_agencies_callback(
                _mk_update(callback_data=f"agency:svcfilter:{self.agent1}:1"), ctx)
            state = self.mod._svc_ui(ctx, self.agent1)
            self.assertEqual(state["filter"], "active")
            upd2 = _mk_update(callback_data=f"agency:svcview:{self.agent1}:{self.svc_active12}:1")
            await self.mod.handle_agencies_callback(upd2, ctx)
            text = _rendered_text(upd2)
            self.assertIn("<b>وضعیت:</b> فعال", text)
            upd3 = _mk_update(callback_data=f"agency:svcback:{self.agent1}")
            await self.mod.handle_agencies_callback(upd3, ctx)
            state2 = self.mod._svc_ui(ctx, self.agent1)
            self.assertEqual(state2["filter"], "active")
            self.assertIn("نمایش: فعال", _rendered_text(upd3))
        _run(flow())

    def test_search_flow_via_text_state(self):
        async def flow():
            ctx = _mk_context()
            ctx.user_data["state"] = self.mod.AGENCY_SVC_SEARCH
            ctx.user_data[self.mod.AGENCY_VIEWING_ID_KEY] = self.agent1
            upd = _mk_update(text="علی مشتری")
            consumed = await self.mod.handle_agencies_text(upd, ctx)
            self.assertTrue(consumed)
            state = self.mod._svc_ui(ctx, self.agent1)
            self.assertEqual(state["query"], "علی مشتری")
            upd2 = _mk_update(callback_data=f"agency:svcback:{self.agent1}")
            await self.mod.handle_agencies_callback(upd2, ctx)
            text = _rendered_text(upd2)
            self.assertIn("علی مشتری", text)
            self.assertNotIn("سرویس دیگر", text)
        _run(flow())

    def test_stale_callback_does_not_crash(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcview:{self.agent1}:424242:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            upd.callback_query.answer.assert_awaited_with("سرویس پیدا نشد.", show_alert=True)
        _run(flow())

    def test_cross_agent_access_rejected(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcview:{self.agent1}:{self.svc_other_agent}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            upd.callback_query.answer.assert_awaited_with("سرویس پیدا نشد.", show_alert=True)
        _run(flow())

    def test_finance_shows_only_linked_transactions(self):
        async def flow():
            ctx = _mk_context()
            agent_db.add_transaction(self.agent1, 250, "purchase", "تمدید سرویس", service_id=self.svc_active12)
            agent_db.add_transaction(self.agent1, 900, "charge", "شارژ کلی")
            upd = _mk_update(callback_data=f"agency:svcfin:{self.agent1}:{self.svc_active12}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("تمدید سرویس", text)
            self.assertIn("خرید/تمدید", text)
            self.assertNotIn("شارژ کلی", text)
            # سرویس بدون تراکنش مرتبط → پیام شفاف
            upd2 = _mk_update(callback_data=f"agency:svcfin:{self.agent1}:{self.svc_inactive}:1")
            await self.mod.handle_agencies_callback(upd2, ctx)
            text2 = _rendered_text(upd2)
            self.assertIn("اتصال مستقیم به این اشتراک ثبت نشده", text2)
            self.assertIn("نسبت داده نمی", text2)
        _run(flow())

    def test_delete_last_item_of_last_page_returns_to_valid_page(self):
        async def flow():
            ctx = _mk_context()
            # بسازیم دقیقاً ۷ سرویس فعال تا صفحه ۲ فقط ۱ آیتم داشته باشد
            a = agent_db.upsert_agent(888, username="pager")
            ids = []
            for i in range(7):
                s = agent_db.create_service(
                    agent_id=a, customer_id=1, server_id=1, server_title="s",
                    name=f"x{i}", panel_user_uuid=f"ux{i}", usage_limit=5, days=9)
                agent_db.update_service(int(s["id"]), {"is_active": 1})
                ids.append(int(s["id"]))
            self.mod._svc_ui(ctx, a)["page"] = 2
            sub = types.ModuleType("AgentBot.services.subscription_service")

            async def fake_delete(agent_id_, service_id_):
                # رفتار واقعی: حذف رکورد محلی + موفقیت
                agent_db.delete_service(service_id_)
                return True

            sub.delete_subscription = AsyncMock(side_effect=fake_delete)
            saved_svc = sys.modules.get("AgentBot.services.subscription_service")
            sys.modules["AgentBot.services.subscription_service"] = sub
            try:
                upd = _mk_update(callback_data=f"agency:svcdeleteok:{a}:{ids[-1]}")
                await self.mod.handle_agencies_callback(upd, ctx)
            finally:
                if saved_svc is not None:
                    sys.modules["AgentBot.services.subscription_service"] = saved_svc
                else:
                    sys.modules.pop("AgentBot.services.subscription_service", None)
            state = self.mod._svc_ui(ctx, a)
            self.assertEqual(state["page"], 1)
        _run(flow())

    def test_more_menu_contains_dangerous_ops_only_there(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcmore:{self.agent1}:{self.svc_active12}")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("عملیات اشتراک", text)
            kb = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            flat = [b.text for row in kb.inline_keyboard for b in row]
            self.assertTrue(any("حذف" in t for t in flat))
            self.assertTrue(any("فعال/غیرفعال" in t for t in flat))
            self.assertTrue(any("تعویض لینک" in t for t in flat))
            # جزئیات اصلی دکمه حذف ندارد
            upd2 = _mk_update(callback_data=f"agency:svcview:{self.agent1}:{self.svc_active12}:1")
            await self.mod.handle_agencies_callback(upd2, ctx)
            kb2 = upd2.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            flat2 = [b.text for row in kb2.inline_keyboard for b in row]
            self.assertFalse(any("حذف" in t for t in flat2))
        _run(flow())

    def test_delete_confirm_shows_name_and_code(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcdelete:{self.agent1}:{self.svc_active12}")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("علی مشتری", text)
            self.assertIn("شناسه اشتراک", text)
            kb = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            flat = [b.text for row in kb.inline_keyboard for b in row]
            self.assertIn("✅ حذف قطعی", flat)
            self.assertIn("❌ انصراف", flat)
        _run(flow())


# ---------------------------------------------------------------------------
#   بازبینی: UTC، منطق انحصاری وضعیت‌ها، جستجوی id، تمدید، حذف، تراکنش خرید
# ---------------------------------------------------------------------------

class TimezoneAndStatusTests(_Base):
    """مورد ۱ و ۲: انقضا بر مبنای UTC + منطق انحصاری وضعیت‌ها."""

    def setUp(self):
        super().setUp()
        # ساعت ثابت UTC برای بازتولید دقیق مرزها (مستقل از timezone ماشین)
        self.fixed_now = datetime(2026, 9, 15, 12, 0, 0)
        self._p1 = patch.object(self.mod, "_utcnow", lambda: self.fixed_now)
        self._p2 = patch.object(agent_db, "_utcnow_naive", lambda: self.fixed_now)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)

    def _mk_at(self, agent_id, name, end_dt, active=1, days_left=30):
        svc = agent_db.create_service(
            agent_id=agent_id, customer_id=1, server_id=1, server_title="s",
            name=name, panel_user_uuid=f"u-{name}", usage_limit=5, days=10,
        )
        end_str = end_dt.strftime(FMT) if end_dt else ""
        agent_db.update_service(int(svc["id"]), {"is_active": active, "end_date": end_str, "days_left": days_left})
        return int(svc["id"])

    def test_two_hours_left_is_not_expired_in_utc(self):
        # سناریوی بازبین: پایان ۲ ساعت دیگر (UTC) نباید منقضی نمایش داده شود
        sid = self._mk_at(self.agent1, "دوساعت", self.fixed_now + timedelta(hours=2))
        svc = agent_db.get_service_by_id(sid)
        parts = self.mod._svc_expiry_parts(svc)
        self.assertFalse(parts["expired"])
        self.assertNotEqual(parts["icon"], "🔴")
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        self.assertNotIn("دوساعت", [r["name"] for r in rows])
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="active")
        self.assertIn("دوساعت", [r["name"] for r in rows])

    def test_really_expired_is_expired(self):
        sid = self._mk_at(self.agent1, "گذشته", self.fixed_now - timedelta(hours=1))
        svc = agent_db.get_service_by_id(sid)
        parts = self.mod._svc_expiry_parts(svc)
        self.assertTrue(parts["expired"])
        self.assertEqual(parts["icon"], "🔴")
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        self.assertIn("گذشته", [r["name"] for r in rows])

    def test_expiry_boundaries_hours(self):
        # مرزهای ۰/۲۴/۴۸/۷۲ ساعت برای وضعیت و نزدیک‌انقضا
        cases = [
            (-1, True, False),    # منقضی
            (0, True, False),     # همین لحظه → منقضی
            (2, False, True),     # ۲ ساعت → نزدیک انقضا
            (23, False, True),
            (24, False, True),    # ۲۴ ساعت → نزدیک
            (48, False, True),    # ۴۸ ساعت → نزدیک
            (72, False, True),    # دقیقاً ۷۲ ساعت → نزدیک (≤ ۳ روز)
            (73, False, False),   # ۷۳ ساعت → نزدیک نیست
        ]
        for hours, exp_expired, exp_near in cases:
            sid = self._mk_at(self.agent1, f"h{hours}", self.fixed_now + timedelta(hours=hours))
            svc = agent_db.get_service_by_id(sid)
            parts = self.mod._svc_expiry_parts(svc)
            self.assertEqual(parts["expired"], exp_expired, msg=f"hours={hours}")
            self.assertEqual(parts["near"], exp_near, msg=f"hours={hours}")

    def test_unknown_expiry_stays_unknown(self):
        sid = self._mk_at(self.agent1, "بی‌زمان", None, days_left=0)
        svc = agent_db.get_service_by_id(sid)
        parts = self.mod._svc_expiry_parts(svc)
        self.assertTrue(parts["unknown_time"])
        self.assertEqual(parts["time"], "نامشخص")
        # جزئیات هم «نامشخص» را نشان دهد و حدس نزند
        text = self.mod._service_detail_text(svc, {"full_name": "x"}, _mk_context(), self.agent1)
        self.assertIn("نامشخص", text)
        # در فیلتر منقضی هم نیاید
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        self.assertNotIn("بی‌زمان", [r["name"] for r in rows])

    def test_stats_exclusive_statuses(self):
        # ۱ فعال معتبر + ۱ منقضی (فعال روی پنل) → آمار انحصاری
        a = agent_db.upsert_agent(5150, username="statsx")
        self._mk_at(a, "سالم", self.fixed_now + timedelta(days=10))
        self._mk_at(a, "فاسد", self.fixed_now - timedelta(hours=2))
        s = agent_db.get_agent_services_stats(a)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["active"], 1)
        self.assertEqual(s["expired"], 1)
        self.assertEqual(s["near_expiry"], 0)
        self.assertEqual(s["inactive"], 0)

    def test_stats_inactive_not_counted_expired(self):
        a = agent_db.upsert_agent(5151, username="statsy")
        self._mk_at(a, "خاموش", self.fixed_now + timedelta(days=10), active=0)
        s = agent_db.get_agent_services_stats(a)
        self.assertEqual((s["inactive"], s["expired"], s["active"]), (1, 0, 0))

    def test_stats_expired_inactive_is_expired_only(self):
        a = agent_db.upsert_agent(5152, username="statsz")
        self._mk_at(a, "خاموش‌وفاسد", self.fixed_now - timedelta(hours=5), active=0)
        s = agent_db.get_agent_services_stats(a)
        self.assertEqual((s["expired"], s["inactive"], s["active"]), (1, 0, 0))

    def test_stats_near_expiry_only_active_positive_within_3d(self):
        a = agent_db.upsert_agent(5153, username="statsw")
        self._mk_at(a, "نزدیک", self.fixed_now + timedelta(hours=30))
        self._mk_at(a, "نزدیک‌خاموش", self.fixed_now + timedelta(hours=30), active=0)
        s = agent_db.get_agent_services_stats(a)
        self.assertEqual(s["near_expiry"], 1)

    def test_stale_days_left_does_not_corrupt_valid_end(self):
        # end_date معتبر آینده + days_left قدیمی منفی → نباید منقضی شود
        sid = self._mk_at(self.agent1, "مغایرت", self.fixed_now + timedelta(days=7), days_left=-3)
        parts = self.mod._svc_expiry_parts(agent_db.get_service_by_id(sid))
        self.assertFalse(parts["expired"])
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, status_filter="expired")
        self.assertNotIn("مغایرت", [r["name"] for r in rows])

    def test_filters_no_overlap_across_statuses(self):
        a = agent_db.upsert_agent(5154, username="statsv")
        self._mk_at(a, "سالم", self.fixed_now + timedelta(days=10))
        self._mk_at(a, "فاسد", self.fixed_now - timedelta(hours=2))
        self._mk_at(a, "خاموش", self.fixed_now + timedelta(days=10), active=0)
        seen = {}
        for f in ("active", "inactive", "expired"):
            rows, _ = agent_db.list_services_by_agent_sorted(a, status_filter=f)
            seen[f] = {r["name"] for r in rows}
        self.assertEqual(seen["active"], {"سالم"})
        self.assertEqual(seen["inactive"], {"خاموش"})
        self.assertEqual(seen["expired"], {"فاسد"})
        # هیچ هم‌پوشانی
        self.assertFalse(seen["active"] & seen["expired"])
        self.assertFalse(seen["inactive"] & seen["expired"])


class InternalIdSearchTests(_Base):
    """مورد ۵: جستجوی id داخلی نمایش‌داده‌شده در دکمه‌ها — تطبیق دقیق.

    برای جداسازی «شرط id» از فیلدهای متنی (که ممکن است خودشان شامل رقم
    باشند)، از پیشوند # استفاده می‌کنیم؛ «#» در هیچ فیلد متنی ظاهر نمی‌شود،
    پس تنها مسیرِ تطبیق، شرط دقیق id است.
    """

    def test_exact_id_match(self):
        target = self.svc_active12
        rows, total = agent_db.list_services_by_agent_sorted(self.agent1, search=f"#{target}")
        self.assertEqual(total, 1)
        self.assertEqual(int(rows[0]["id"]), target)

    def test_nonexistent_id_returns_nothing(self):
        # «#<id>99» وجود ندارد؛ تطبیق فازیِ id چیزی برنمی‌گرداند (تطبیق دقیق)
        rows, total = agent_db.list_services_by_agent_sorted(
            self.agent1, search=f"#{self.svc_active12}99")
        self.assertEqual((rows, total), ([], 0))

    def test_plain_digits_exact_id_also_matched(self):
        target = self.svc_active12
        rows, _ = agent_db.list_services_by_agent_sorted(self.agent1, search=str(target))
        self.assertTrue(any(int(r["id"]) == target for r in rows))

    def test_id_search_scoped_to_agent(self):
        # id سرویس نماینده دوم با جستجوی نماینده اول پیدا نمی‌شود
        rows, total = agent_db.list_services_by_agent_sorted(self.agent1, search=f"#{self.svc_other_agent}")
        self.assertEqual(total, 0)

    def test_non_digit_terms_unaffected(self):
        rows, total = agent_db.list_services_by_agent_sorted(self.agent1, search="علی مشتری")
        self.assertEqual(total, 1)


class RenewFlowTests(_Base):
    """مورد ۳: دکمه تمدید، انتخاب پلن، تأیید و اجرا با سرویس معتبر موجود."""

    def setUp(self):
        super().setUp()
        self.plan_id = int(agent_db.set_agent_plan(
            self.agent1, server_id=1, days=30, gb=20.0,
            wholesale_price=50000, sale_price=80000, plan_title="ماهانه",
        )["id"])

    def test_detail_page_has_renew_button(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcview:{self.agent1}:{self.svc_active12}:1")
            await self.mod.handle_agencies_callback(upd, ctx)
            kb = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            flat = [b.text for row in kb.inline_keyboard for b in row]
            self.assertIn("♻️ تمدید", flat)
        _run(flow())

    def test_renew_without_plans_shows_message_not_dead_button(self):
        async def flow():
            # نماینده بدون هیچ پلنی
            a2 = agent_db.upsert_agent(4321, username="noplan")
            svc = agent_db.create_service(
                agent_id=a2, customer_id=1, server_id=1, server_title="s",
                name="بی‌پلن", panel_user_uuid="np", usage_limit=5, days=10)
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcrenew:{a2}:{int(svc['id'])}")
            await self.mod.handle_agencies_callback(upd, ctx)
            upd.callback_query.answer.assert_awaited_with("پلن معتبری برای تمدید تعریف نشده است.", show_alert=True)
        _run(flow())

    def test_renew_plan_list_and_confirm_show_real_values(self):
        async def flow():
            ctx = _mk_context()
            agent_db.charge_wallet(self.agent1, 60000)
            upd = _mk_update(callback_data=f"agency:svcrenew:{self.agent1}:{self.svc_active12}")
            await self.mod.handle_agencies_callback(upd, ctx)
            kb = upd.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            plan_btns = [b for row in kb.inline_keyboard for b in row
                         if b.callback_data and "svcrenewplan" in b.callback_data]
            self.assertEqual(len(plan_btns), 1)
            # برچسب دکمه با مقادیر واقعی پلن
            self.assertIn("30 روز", plan_btns[0].text)
            self.assertIn("20GB", plan_btns[0].text)
            self.assertIn("50,000", plan_btns[0].text)
            plan_cb = plan_btns[0].callback_data
            # تأیید: نام، شناسه، حجم، مدت، قیمت عمده، موجودی
            upd2 = _mk_update(callback_data=plan_cb)
            await self.mod.handle_agencies_callback(upd2, ctx)
            text2 = _rendered_text(upd2)
            self.assertIn("حجم جدید: <b>20GB</b>", text2)
            self.assertIn("مدت جدید: <b>30 روز</b>", text2)
            self.assertIn("قیمت عمده: <b>50,000</b>", text2)
            self.assertIn("60,000", text2)  # موجودی کیف پول
            kb2 = upd2.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            do_cb = [b.callback_data for row in kb2.inline_keyboard for b in row
                     if b.callback_data and "svcrenewdo" in b.callback_data]
            self.assertEqual(len(do_cb), 1)
            self.assertLessEqual(len(do_cb[0]), 64)
        _run(flow())

    def test_renew_confirm_insufficient_balance_warns(self):
        async def flow():
            ctx = _mk_context()
            agent_db.charge_wallet(self.agent1, 1000)  # کمتر از 50000
            upd = _mk_update(callback_data=f"agency:svcrenewplan:{self.agent1}:{self.svc_active12}:{self.plan_id}")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("موجودی کیف پول نماینده کمتر از قیمت عمده", text)
        _run(flow())

    def test_renew_do_success_calls_existing_service_and_renders_detail(self):
        async def flow():
            sub = types.ModuleType("AgentBot.services.subscription_service")

            async def fake_renew(agent_id_, service_id_, extra_days=0, extra_gb=0.0, **kw):
                self.assertEqual(extra_days, 30)
                self.assertEqual(extra_gb, 20.0)
                agent_db.renew_service(service_id_, extra_days=extra_days, extra_gb=extra_gb)
                return agent_db.get_service_by_id(service_id_)

            sub.renew_subscription = AsyncMock(side_effect=fake_renew)
            saved = sys.modules.get("AgentBot.services.subscription_service")
            sys.modules["AgentBot.services.subscription_service"] = sub
            try:
                ctx = _mk_context()
                upd = _mk_update(callback_data=f"agency:svcrenewdo:{self.agent1}:{self.svc_active12}:{self.plan_id}")
                await self.mod.handle_agencies_callback(upd, ctx)
            finally:
                if saved is not None:
                    sys.modules["AgentBot.services.subscription_service"] = saved
                else:
                    sys.modules.pop("AgentBot.services.subscription_service", None)
            upd.callback_query.answer.assert_awaited_with("✅ تمدید انجام شد.")
            text = _rendered_text(upd)
            # جزئیات با اطلاعات تازه (انقضای جدید ~۳۰ روز)
            self.assertIn("<b>وضعیت:</b> فعال", text)
        _run(flow())

    def test_renew_failure_shows_no_success(self):
        async def flow():
            sub = types.ModuleType("AgentBot.services.subscription_service")
            sub.renew_subscription = AsyncMock(return_value=None)  # شکست کامل
            saved = sys.modules.get("AgentBot.services.subscription_service")
            sys.modules["AgentBot.services.subscription_service"] = sub
            try:
                ctx = _mk_context()
                upd = _mk_update(callback_data=f"agency:svcrenewdo:{self.agent1}:{self.svc_active12}:{self.plan_id}")
                await self.mod.handle_agencies_callback(upd, ctx)
            finally:
                if saved is not None:
                    sys.modules["AgentBot.services.subscription_service"] = saved
                else:
                    sys.modules.pop("AgentBot.services.subscription_service", None)
            args = upd.callback_query.answer.await_args
            self.assertIn("انجام نشد", str(args))
        _run(flow())

    def test_renew_rejects_other_agent_service(self):
        async def flow():
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcrenew:{self.agent1}:{self.svc_other_agent}")
            await self.mod.handle_agencies_callback(upd, ctx)
            upd.callback_query.answer.assert_awaited_with("سرویس پیدا نشد.", show_alert=True)
        _run(flow())


class DeleteTargetsTests(_Base):
    """مورد ۴: مقصدهای حذف از داده ذخیره‌شده + متن هم‌سو با رفتار."""

    def setUp(self):
        super().setUp()
        # servers.json موقت با سرور اصلی و نود
        self._tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp2.cleanup)
        servers_path = Path(self._tmp2.name) / "servers.json"
        servers_path.write_text(json.dumps({
            "servers": [
                {"id": 1, "title": "<اصلی&>"},
                {"id": 2, "title": "نود دوم"},
            ],
        }), encoding="utf-8")
        p = patch.object(shared_database, "DB_PATH", str(servers_path))
        p.start()
        self.addCleanup(p.stop)

    def test_confirm_shows_stored_targets_and_correct_destination(self):
        async def flow():
            agent_db.add_service_node(self.svc_active12, server_id=2, server_title="نود دوم",
                                      panel_user_uuid="n1", marzban_username="")
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcdelete:{self.agent1}:{self.svc_active12}")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("علی مشتری", text)
            self.assertIn("شناسه اشتراک", text)
            # مقصدها escape شده‌اند
            self.assertIn("&lt;اصلی&amp;&gt;", text)
            self.assertIn("نود دوم", text)
            # متن مقصد اشتباه حذف شده؛ مقصد واقعی: لیست اشتراک‌ها
            self.assertNotIn("پروفایل نماینده", text)
            self.assertIn("لیست اشتراک‌ها", text)
        _run(flow())

    def test_unknown_targets_message(self):
        async def flow():
            # سرور اصلی و نگاشت نود در داده‌ها وجود ندارند
            svc = agent_db.create_service(
                agent_id=self.agent1, customer_id=1, server_id=999, server_title="",
                name="بی‌مقصد", panel_user_uuid="bm", usage_limit=5, days=10)
            ctx = _mk_context()
            upd = _mk_update(callback_data=f"agency:svcdelete:{self.agent1}:{int(svc['id'])}")
            await self.mod.handle_agencies_callback(upd, ctx)
            text = _rendered_text(upd)
            self.assertIn("مقصدهای پنل از داده ذخیره‌شده قابل تشخیص نیست", text)
        _run(flow())

    def test_delete_last_item_returns_to_valid_page_preserving_state(self):
        async def flow():
            ctx = _mk_context()
            state = self.mod._svc_ui(ctx, self.agent1)
            state["filter"] = "active"
            state["page"] = 1
            # فقط دو سرویس فعال → پس از حذف یکی صفحه ۱ معتبر می‌ماند و فیلتر حفظ است
            with patch.dict(os.environ, {"ADMIN_ID": str(ADMIN_ID)}):
                sub = types.ModuleType("AgentBot.services.subscription_service")

                async def fake_delete(agent_id_, service_id_):
                    agent_db.delete_service(service_id_)
                    return True

                sub.delete_subscription = AsyncMock(side_effect=fake_delete)
                saved = sys.modules.get("AgentBot.services.subscription_service")
                sys.modules["AgentBot.services.subscription_service"] = sub
                try:
                    upd = _mk_update(callback_data=f"agency:svcdeleteok:{self.agent1}:{self.svc_near1}")
                    await self.mod.handle_agencies_callback(upd, ctx)
                finally:
                    if saved is not None:
                        sys.modules["AgentBot.services.subscription_service"] = saved
                    else:
                        sys.modules.pop("AgentBot.services.subscription_service", None)
            self.assertEqual(state["filter"], "active")
            text = _rendered_text(upd)
            self.assertIn("نمایش: فعال", text)
        _run(flow())


class TransactionLinkTests(_Base):
    """مورد ۶: اتصال قطعی تراکنش خرید اولیه به سرویس + عدم کسر دوباره."""

    def test_attach_links_only_unlinked_and_own_agent(self):
        tx = agent_db.add_transaction(self.agent1, 100, "purchase", "خرید A", service_id=0)
        self.assertTrue(agent_db.attach_transaction_to_service(tx, self.agent1, self.svc_active12))
        linked = agent_db.get_service_transactions(self.svc_active12, self.agent1)
        self.assertEqual(len(linked), 1)
        # اتصال دوباره به سرویس دیگر انجام نمی‌شود
        self.assertFalse(agent_db.attach_transaction_to_service(tx, self.agent1, self.svc_near1))
        # تراکنش نماینده دیگر متصل نمی‌شود
        tx2 = agent_db.add_transaction(self.agent1, 200, "purchase", "خرید B", service_id=0)
        self.assertFalse(agent_db.attach_transaction_to_service(tx2, self.agent2, self.svc_other_agent))
        rows = agent_db.get_service_transactions(self.svc_other_agent, self.agent2)
        self.assertEqual(len(rows), 0)

    def test_create_subscription_links_purchase_to_service(self):
        from AgentBot.services import subscription_service as subs
        agent_db.charge_wallet(self.agent1, 100000)
        customer_id = agent_db.upsert_customer(self.agent1, 4455, full_name="خریدار")
        server = {"id": 1, "title": "srv", "panel_type": "hiddify"}

        async def fake_cluster_create(targets, payload):
            return ({"uuid": "u-new"}, [
                {"server_id": 1, "server_title": "srv", "panel_user_uuid": "u-new", "marzban_username": ""},
            ])

        with patch.object(subs, "get_server_by_id", return_value=server), \
             patch.object(subs, "_get_cluster_servers", return_value=[server]), \
             patch.object(subs, "_create_user_on_cluster", new=AsyncMock(side_effect=fake_cluster_create)):
            updated = _run(subs.create_subscription(self.agent1, customer_id, 1, {
                "days": 30, "gb": 10.0, "wholesale_price": 5000, "sale_price": 7000,
            }, "خرید اولیه"))
        self.assertIsNotNone(updated)
        # تراکنش خرید اولیه به سرویس واقعی متصل است
        linked = agent_db.get_service_transactions(int(updated["id"]), self.agent1)
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["tx_type"], "purchase")
        self.assertEqual(int(linked[0]["amount"]), 5000)
        # بدون کسر دوباره
        w = agent_db.get_wallet(self.agent1)
        self.assertEqual(int(w["balance"]), 95000)

    def test_create_failure_leaves_refund_unlinked(self):
        from AgentBot.services import subscription_service as subs
        agent_db.charge_wallet(self.agent1, 100000)
        customer_id = agent_db.upsert_customer(self.agent1, 4456, full_name="خریدار۲")
        server = {"id": 1, "title": "srv", "panel_type": "hiddify"}

        async def boom(targets, payload):
            raise RuntimeError("panel down")

        with patch.object(subs, "get_server_by_id", return_value=server), \
             patch.object(subs, "_get_cluster_servers", return_value=[server]), \
             patch.object(subs, "_create_user_on_cluster", new=AsyncMock(side_effect=boom)):
            result = _run(subs.create_subscription(self.agent1, customer_id, 1, {
                "days": 30, "gb": 10.0, "wholesale_price": 5000, "sale_price": 7000,
            }, "خرید ناموفق"))
        self.assertIsNone(result)
        w = agent_db.get_wallet(self.agent1)
        self.assertEqual(int(w["balance"]), 100000)  # بازگشت کامل
        # هیچ سرویسی ساخته نشده و تراکنش purchase به سرویسی دروغ متصل نشده
        txs, _total = agent_db.get_transactions(self.agent1, page=1, page_size=20)
        self.assertFalse(any(t["service_id"] != 0 for t in txs))

    def test_general_wallet_charge_not_in_service_history(self):
        agent_db.add_transaction(self.agent1, 900, "charge", "شارژ کلی", service_id=0)
        rows = agent_db.get_service_transactions(self.svc_active12, self.agent1)
        self.assertFalse(any(t["description"] == "شارژ کلی" for t in rows))


class CallbackDataLimitTests(_Base):
    """تأیید طول callback_data (≤۶۴ بایت) در همه دکمه‌های مسیر سرویس‌ها."""

    def test_all_service_callbacks_within_limit(self):
        async def collect(update_cb, ctx):
            upd = _mk_update(callback_data=update_cb)
            await self.mod.handle_agencies_callback(upd, ctx)
            call = upd.callback_query.edit_message_text.await_args
            if call is None:
                return []
            kb = call.kwargs.get("reply_markup")
            if kb is None:
                return []
            return [b.callback_data for row in kb.inline_keyboard for b in row
                    if getattr(b, "callback_data", None)]

        flows = [
            f"agency:svcview:{self.agent1}:{self.svc_active12}:1",
            f"agency:svcrenew:{self.agent1}:{self.svc_active12}",
            f"agency:svcrenewplan:{self.agent1}:{self.svc_active12}:1",
            f"agency:svcdelete:{self.agent1}:{self.svc_active12}",
            f"agency:svcmore:{self.agent1}:{self.svc_active12}",
            f"agency:svcfin:{self.agent1}:{self.svc_active12}:1",
        ]
        agent_db.set_agent_plan(self.agent1, server_id=1, days=30, gb=20.0,
                                wholesale_price=50000, sale_price=0, plan_title="م")
        for cb in flows:
            datas = _run(collect(cb, _mk_context()))
            for d in datas:
                self.assertLessEqual(len(d.encode()), 64, msg=d)


if __name__ == "__main__":
    unittest.main()
