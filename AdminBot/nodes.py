# AdminBot/nodes.py
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from html import escape

from telegram import (
    Update,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest

# اضافه کردن ریشه پروژه به sys.path تا Shared و بقیه ماژول‌ها پیدا شوند
ROOT_DIR = Path(__file__).resolve().parents[1]  # پوشه‌ی Hiddify-SellBot
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Shared import database, hiddify_api, node_ops
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from AdminBot.keyboards import admin_main_keyboard, cancel_keyboard

logger = logging.getLogger(__name__)

# استیت‌های ویزارد نود
NODES_STATE_ADD_TITLE = "nodes_add_title"
NODES_STATE_ADD_PANEL = "nodes_add_panel"
NODES_STATE_ADD_ADMIN_PROXY = "nodes_add_admin_proxy"
NODES_STATE_ADD_ADMIN_UUID = "nodes_add_admin_uuid"
NODES_STATE_ADD_USER_PROXY = "nodes_add_user_proxy"
NODES_STATE_ADD_DOMAIN = "nodes_add_domain"
NODES_STATE_ADD_LIMIT = "nodes_add_limit"
NODES_STATE_AUTO_TITLE = "nodes_auto_title"
NODES_STATE_AUTO_PANEL = "nodes_auto_panel"
NODES_STATE_AUTO_ADMIN_PROXY = "nodes_auto_admin_proxy"
NODES_STATE_AUTO_ADMIN_UUID = "nodes_auto_admin_uuid"
NODES_STATE_AUTO_USER_PROXY = "nodes_auto_user_proxy"
NODES_STATE_AUTO_DOMAIN = "nodes_auto_domain"
NODES_STATE_AUTO_LIMIT = "nodes_auto_limit"
NODES_STATE_REAL_TITLE = "nodes_real_title"
NODES_STATE_REAL_LIMIT = "nodes_real_limit"
NODES_STATE_EDIT_TITLE = "nodes_edit_title"
NODES_STATE_EDIT_USER_PROXY = "nodes_edit_user_proxy"
NODES_STATE_EDIT_ADMIN_PROXY = "nodes_edit_admin_proxy"
NODES_STATE_EDIT_ADMIN_UUID = "nodes_edit_admin_uuid"
NODES_STATE_EDIT_PANEL_URL = "nodes_edit_panel_url"
NODES_STATE_EDIT_USERS_LIMIT = "nodes_edit_users_limit"

# کلمات لغو
CANCEL_WORDS = {"لغو❌", "❌لغو", "لغو", "/cancel"}


def _is_cancel(text: str) -> bool:
    t = (text or "").strip()
    for ch in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u2066", "\u2067", "\u2068", "\u2069"):
        t = t.replace(ch, "")
    t = t.replace(" ", "")
    return t in CANCEL_WORDS


def _short_error(exc: Exception, max_len: int = 320) -> str:
    """
    خطا را کوتاه و قابل‌نمایش در تلگرام می‌کند تا از Message too long جلوگیری شود.
    """
    raw = " ".join(str(exc).split())
    lower = raw.lower()

    if "just a moment" in lower or "__cf_chl" in lower or "cloudflare" in lower:
        return "دسترسی توسط Cloudflare مسدود شده است (HTTP 403). روی دامنه پنل، WAF/Bot Challenge را برای API غیرفعال کنید."

    if len(raw) > max_len:
        return raw[: max_len - 3] + "..."
    return raw


def _normalize_domain_input(text: str) -> str:
    """
    Normalize domain/url input for server domains list.
    Accepts:
      - user.example.com
      - https://user.example.com
    Returns normalized host or full scheme+host (without path/query).
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    if raw.startswith("http://") or raw.startswith("https://"):
        p = urlparse(raw)
        host = (p.netloc or "").strip()
        if not host:
            return ""
        return f"{p.scheme}://{host}"

    # host only mode (no path allowed)
    if "/" in raw or "?" in raw or "#" in raw:
        return ""
    return raw


# ===============================
#   Helper برای ذخیره/خواندن نودها
# ===============================

def _normalize_nodes(raw_nodes: Any) -> List[Dict[str, Any]]:
    """
    لیست نودها را به فرم استاندارد:
    [{"id": int, "title": str, "host": str}, ...]
    تبدیل می‌کند.
    """
    nodes: List[Dict[str, Any]] = []
    if not raw_nodes:
        return nodes

    for idx, n in enumerate(raw_nodes, start=1):
        if isinstance(n, dict):
            nid = n.get("id") or idx
            title = n.get("title") or n.get("name") or f"نود #{nid}"
            host = n.get("host") or n.get("domain") or n.get("ip") or ""
        else:
            nid = idx
            title = f"نود #{nid}"
            host = str(n)

        host = str(host).strip()
        if not host:
            continue

        nodes.append(
            {
                "id": int(nid),
                "title": str(title),
                "host": host,
                "target_server_id": None,
            }
        )
        if isinstance(n, dict):
            try:
                if n.get("target_server_id") is not None:
                    nodes[-1]["target_server_id"] = int(n.get("target_server_id"))
            except (TypeError, ValueError):
                nodes[-1]["target_server_id"] = None

    # مرتب‌سازی بر اساس id
    nodes.sort(key=lambda x: x["id"])
    return nodes


def _load_nodes_for_server(server_id: int) -> List[Dict[str, Any]]:
    server = database.get_server_by_id(server_id)
    if not server:
        return []
    raw_nodes = server.get("nodes") or []
    return _normalize_nodes(raw_nodes)


def _save_nodes_for_server(server_id: int, nodes: List[Dict[str, Any]]) -> None:
    server = database.get_server_by_id(server_id)
    if not server:
        raise RuntimeError("Server not found when saving nodes")
    database.update_server(server_id, {"nodes": nodes})


def _find_node(server_id: int, node_id: int) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    nodes = _load_nodes_for_server(server_id)
    for idx, n in enumerate(nodes):
        if int(n.get("id") or 0) == int(node_id):
            return nodes, n, idx
    return nodes, None, -1


def _build_node_edit_text(server_id: int, node: Dict[str, Any]) -> str:
    target_sid = int(node.get("target_server_id") or 0)
    child = database.get_server_by_id(target_sid) if target_sid > 0 else None
    panel_url = (child or {}).get("panel_url") or ""
    admin_proxy = (child or {}).get("admin_proxy_path") or ""
    admin_uuid = (child or {}).get("admin_uuid") or ""
    users_limit = (child or {}).get("users_limit")
    users_limit_text = str(users_limit) if users_limit not in (None, "") else "—"
    domains = (child or {}).get("domains") or []
    domain = "—"
    if domains:
        first = domains[0]
        if isinstance(first, dict):
            domain = str(first.get("domain") or first.get("host") or first.get("url") or "—")
        else:
            domain = str(first)

    def _to_clickable_url(raw: Any) -> str:
        value = str(raw or "").strip()
        if not value or value == "—":
            return ""
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://{value}"

    def _safe_text(raw: Any, fallback: str = "—") -> str:
        value = str(raw if raw not in (None, "") else fallback)
        return escape(value)

    panel_link = _to_clickable_url(panel_url)
    server_panel_link = ""
    if panel_link:
        base = panel_link.rstrip("/")
        ap = str(admin_proxy or "").strip().strip("/")
        au = str(admin_uuid or "").strip().strip("/")
        if ap and au:
            server_panel_link = f"{base}/{ap}/{au}/"
        elif ap:
            server_panel_link = f"{base}/{ap}/"
        else:
            server_panel_link = base
    server_title = _safe_text(node.get("title") or "—")
    if server_panel_link:
        server_line = f'🖥 سرور: <a href="{escape(server_panel_link, quote=True)}">{server_title}</a>'
    else:
        server_line = f"🖥 سرور: {server_title}"
    node_id_text = f"\u200e{int(node.get('id') or 0)}\u200e"
    server_id_text = f"\u200e{target_sid or '—'}\u200e"
    domain_text = str(domain or "").strip().rstrip("/") or "—"
    panel_text = str(panel_url or "").strip().rstrip("/") or "—"

    return (
        "✏️ ویرایش نود\n"
        "❖⬩──────────────⬩❖\n"
        f"{server_line}\n"
        f"🗄️ سرور: {escape(server_id_text)} | 🆔 نود: {escape(node_id_text)}\n"
        f"🔗 دامنه ساب: {_safe_text(domain_text)}\n"
        f"📡 آدرس پنل: {_safe_text(panel_text)}\n"
        f"👤 محدودیت کاربران: {_safe_text(users_limit_text)}\n\n"
        "فیلد موردنظر را انتخاب کنید:"
    )


def _build_node_edit_keyboard(server_id: int, node_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ ویرایش عنوان", callback_data=f"nodeedit:{server_id}:{node_id}:title")],
            [InlineKeyboardButton("📚 لیست دامنه‌ها", callback_data=f"nodeedit:{server_id}:{node_id}:domains")],
            [InlineKeyboardButton("🔐 ویرایش Proxy کاربر", callback_data=f"nodeedit:{server_id}:{node_id}:user_proxy")],
            [InlineKeyboardButton("🔐 ویرایش Proxy ادمین", callback_data=f"nodeedit:{server_id}:{node_id}:admin_proxy")],
            [InlineKeyboardButton("🔑 ویرایش UUID ادمین", callback_data=f"nodeedit:{server_id}:{node_id}:admin_uuid")],
            [InlineKeyboardButton("🌐 ویرایش آدرس پنل", callback_data=f"nodeedit:{server_id}:{node_id}:panel_url")],
            [InlineKeyboardButton("👤 ویرایش محدودیت کاربران", callback_data=f"nodeedit:{server_id}:{node_id}:users_limit")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"nodes:{server_id}:back")],
        ]
    )


async def send_node_edit_menu(
    server_id: int,
    node_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    _, node, _ = _find_node(server_id, node_id)
    if not node:
        text = "❌ نود مورد نظر پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    text = _build_node_edit_text(server_id, node)
    kb = _build_node_edit_keyboard(server_id, node_id)
    if message is not None:
        await message.edit_text(
            text,
            reply_markup=kb,
            disable_web_page_preview=True,
            parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id,
            text,
            reply_markup=kb,
            disable_web_page_preview=True,
            parse_mode="HTML",
        )


# ===============================
#   منوی لیست نودها
# ===============================

async def send_nodes_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """منوی اصلی لیست نودهای یک سرور"""
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    nodes = _load_nodes_for_server(server_id)

    lines = [
        "⚙️ مدیریت نودها",
        "⬇️ لیست نود های شما",
        "",
    ]

    if not nodes:
        lines.append("در حال حاضر هیچ نودی ثبت نشده است.")
    else:
        lines.append("نودهای ثبت‌شده:")
        # فقط عنوان هر نود را نشان می‌دهیم؛ آدرس (host) در متن نمی‌آید
        for n in nodes:
            lines.append(f"• {n['title']}")

    # ---------- دکمه‌ها ----------
    rows: List[List[InlineKeyboardButton]] = []

    # دکمه‌ی هر نود (اگر نودی وجود داشته باشد)
    for n in nodes:
        rows.append(
            [
                InlineKeyboardButton(
                    n["title"],
                    callback_data=f"nodeinfo:{server_id}:{n['id']}",
                )
            ]
        )

    # دکمه‌های عملیاتی
    rows.append(
        [InlineKeyboardButton("➕ افزودن", callback_data=f"nodes:{server_id}:add")]
    )
    if nodes:
        rows.append(
            [InlineKeyboardButton("🗑 حذف", callback_data=f"nodes:{server_id}:remove")]
        )

    # 🔙 دکمه بازگشت همیشه اضافه می‌شود (چه نود باشد چه نباشد)
    rows.append(
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"server:{server_id}")]
    )

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if message is not None:
        try:
            await message.edit_text(text, reply_markup=kb)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)



async def send_nodes_delete_menu(
    server_id: int,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message=None,
) -> None:
    """نمایش لیست نودها برای حذف"""
    server = database.get_server_by_id(server_id)
    if not server:
        text = "❌ سرور پیدا نشد."
        if message is not None:
            await message.edit_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return

    nodes = _load_nodes_for_server(server_id)

    if not nodes:
        text = "هیچ نودی برای حذف وجود ندارد."
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"nodes:{server_id}:back")]]
        )
        if message is not None:
            await message.edit_text(text, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, reply_markup=kb)
        return

    # ===== متن بالای منو =====
    lines = [
        "🗑 حذف نود",
        "یکی از نودهای زیر را برای حذف انتخاب کنید:",
        "",
    ]

    # اینجا فقط عنوان رو می‌نویسیم؛ آدرس رو حذف می‌کنیم
    for n in nodes:
        lines.append(f"• {n['title']}")

    # ===== دکمه‌ها =====
    rows: List[List[InlineKeyboardButton]] = []
    for n in nodes:
        rows.append(
            [
                InlineKeyboardButton(
                    n["title"],
                    callback_data=f"delnode:{server_id}:{n['id']}",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"nodes:{server_id}:back")]
    )

    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)

    if message is not None:
        await message.edit_text(text, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, reply_markup=kb)


# ===============================
#   ویزارد افزودن نود (state)
# ===============================

async def handle_add_node_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    افزودن دستی نود با اطلاعات کامل پنل:
    عنوان -> panel_url -> admin_proxy -> admin_uuid -> user_proxy -> domain -> users_limit
    سپس تست اتصال و ثبت نود.
    """
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    # لغو در هر مرحله
    if _is_cancel(text):
        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_node", None)
        await message.reply_text(
            "❌ افزودن نود لغو شد.",
            reply_markup=admin_main_keyboard(),
        )
        return

    server_id = context.user_data.get("nodes_server_id")
    if server_id is None:
        context.user_data.pop("state", None)
        context.user_data.pop("new_node", None)
        await message.reply_text(
            "❌ وضعیت سرور برای افزودن نود نامشخص است. دوباره از منوی «لیست نودها» اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    new_node = context.user_data.get("new_node") or {}

    # مرحله ۱: گرفتن عنوان
    if state == NODES_STATE_ADD_TITLE:
        if not text:
            await message.reply_text("❌ عنوان نود نمی‌تواند خالی باشد.", reply_markup=cancel_keyboard())
            return
        new_node["title"] = text
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_PANEL

        await message.reply_text(
            "🌐 آدرس پنل نود را وارد کنید:\nمثال: https://node.example.com",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_PANEL:
        panel_url = text.strip()
        if not (panel_url.startswith("http://") or panel_url.startswith("https://")):
            await message.reply_text(
                "❌ آدرس پنل باید با http/https باشد.\nمثال: https://node.example.com",
                reply_markup=cancel_keyboard(),
            )
            return
        new_node["panel_url"] = panel_url.rstrip("/")
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_ADMIN_PROXY
        await message.reply_text(
            "🔐 Proxy Path ادمین را وارد کنید (بدون /):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_ADMIN_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["admin_proxy_path"] = val
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_ADMIN_UUID
        await message.reply_text(
            "🔑 UUID/API Key ادمین را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_ADMIN_UUID:
        val = text.strip()
        if not val:
            await message.reply_text("❌ UUID/API Key ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["admin_uuid"] = val
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_USER_PROXY
        await message.reply_text(
            "🔐 Proxy Path کاربر را وارد کنید (بدون /):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_USER_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path کاربر خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["user_proxy_path"] = val
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_DOMAIN
        await message.reply_text(
            "🌍 دامنه ساب نود را وارد کنید:\n"
            "مثال: user.node-example.com\n"
            "یا: https://user.node-example.com",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_DOMAIN:
        val = _normalize_domain_input(text)
        if not val:
            await message.reply_text(
                "❌ دامنه معتبر نیست.\n"
                "مثال صحیح: user.node-example.com",
                reply_markup=cancel_keyboard(),
            )
            return
        new_node["domain"] = val
        context.user_data["new_node"] = new_node
        context.user_data["state"] = NODES_STATE_ADD_LIMIT
        await message.reply_text(
            "👤 محدودیت کاربران نود را وارد کنید (عدد):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_ADD_LIMIT:
        try:
            users_limit = int(text.strip())
            if users_limit <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً عدد معتبر بزرگ‌تر از صفر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        panel_url = str(new_node.get("panel_url") or "").rstrip("/")
        title = str(new_node.get("title") or "Node")
        admin_proxy = str(new_node.get("admin_proxy_path") or "").strip("/")
        admin_uuid = str(new_node.get("admin_uuid") or "").strip()
        user_proxy = str(new_node.get("user_proxy_path") or "").strip("/")
        domain = str(new_node.get("domain") or "").strip()
        host = urlparse(panel_url).hostname or panel_url

        new_server_payload = {
            "title": title,
            "panel_url": panel_url,
            "admin_proxy_path": admin_proxy,
            "admin_uuid": admin_uuid,
            "user_proxy_path": user_proxy,
            "users_limit": int(users_limit),
            "priority": 0,
            "version": 11,
            "users": [],
            "plans": [],
            "domains": ([{"id": 1, "title": domain, "domain": domain}] if domain else []),
            "nodes": [],
        }

        try:
            await message.reply_text("⏳ در حال تست اتصال پنل نود...")
            await hiddify_api.list_users(new_server_payload)
        except Exception as e:
            await message.reply_text(
                f"❌ اتصال به پنل نود برقرار نشد:\n{_short_error(e)}",
                reply_markup=cancel_keyboard(),
            )
            return

        try:
            created_server = database.add_server(new_server_payload)
            child_server_id = int(created_server.get("id") or 0)

            nodes = _load_nodes_for_server(server_id)
            new_id = (max((n["id"] for n in nodes), default=0) + 1)
            nodes.append(
                {
                    "id": new_id,
                    "title": title,
                    "host": host,
                    "target_server_id": child_server_id,
                }
            )
            _save_nodes_for_server(server_id, nodes)
        except Exception as e:
            logger.exception("Error saving manual node: %s", e)
            await message.reply_text(
                f"❌ خطا در ذخیره نود:\n{_short_error(e)}",
                reply_markup=cancel_keyboard(),
            )
            return

        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_node", None)

        await message.reply_text(
            f"✅ نود «{title}» با موفقیت اضافه شد و اتصال آن تایید شد.\n"
            f"🆔 سرور نود: {child_server_id}\n"
            f"🌐 آدرس: {host}",
            reply_markup=admin_main_keyboard(),
        )
        await send_nodes_menu(server_id, message.chat_id, context)
        return

    # اگر state شناخته نشد
    context.user_data.pop("state", None)
    await message.reply_text(
        "❌ وضعیت افزودن نود نامعتبر است. دوباره از منوی «لیست نودها» اقدام کنید.",
        reply_markup=admin_main_keyboard(),
    )


async def handle_auto_add_node_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    ساخت نود (سرور جدید) از داخل منوی نودها:
    عنوان -> panel_url -> admin_proxy -> admin_uuid -> user_proxy -> domain -> users_limit
    """
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    if _is_cancel(text):
        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_auto_node", None)
        await message.reply_text(
            "❌ ساخت خودکار نود لغو شد.",
            reply_markup=admin_main_keyboard(),
        )
        return

    parent_server_id = context.user_data.get("nodes_server_id")
    if parent_server_id is None:
        context.user_data.pop("state", None)
        context.user_data.pop("new_auto_node", None)
        await message.reply_text(
            "❌ سرور مبدا برای ساخت نود مشخص نیست. دوباره از منوی «لیست نودها» اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    new_node = context.user_data.get("new_auto_node") or {}

    if state == NODES_STATE_AUTO_TITLE:
        if not text:
            await message.reply_text("❌ عنوان نود نمی‌تواند خالی باشد.", reply_markup=cancel_keyboard())
            return
        new_node["title"] = text
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_PANEL
        await message.reply_text(
            "🌐 آدرس پنل نود را وارد کنید:\nمثال: https://node.example.com",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_PANEL:
        panel_url = text.strip()
        if not (panel_url.startswith("http://") or panel_url.startswith("https://")):
            await message.reply_text(
                "❌ آدرس پنل باید با http/https باشد.\nمثال: https://node.example.com",
                reply_markup=cancel_keyboard(),
            )
            return
        new_node["panel_url"] = panel_url.rstrip("/")
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_ADMIN_PROXY
        await message.reply_text(
            "🔐 Proxy Path ادمین را وارد کنید (بدون /):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_ADMIN_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["admin_proxy_path"] = val
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_ADMIN_UUID
        await message.reply_text(
            "🔑 UUID/API Key ادمین را وارد کنید:",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_ADMIN_UUID:
        val = text.strip()
        if not val:
            await message.reply_text("❌ UUID/API Key ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["admin_uuid"] = val
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_USER_PROXY
        await message.reply_text(
            "🔐 Proxy Path کاربر را وارد کنید (بدون /):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_USER_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path کاربر خالی است.", reply_markup=cancel_keyboard())
            return
        new_node["user_proxy_path"] = val
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_DOMAIN
        await message.reply_text(
            "🌍 دامنه ساب نود را وارد کنید:\n"
            "مثال: user.node-example.com\n"
            "یا: https://user.node-example.com",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_DOMAIN:
        val = _normalize_domain_input(text)
        if not val:
            await message.reply_text(
                "❌ دامنه معتبر نیست.\n"
                "مثال صحیح: user.node-example.com",
                reply_markup=cancel_keyboard(),
            )
            return
        new_node["domain"] = val
        context.user_data["new_auto_node"] = new_node
        context.user_data["state"] = NODES_STATE_AUTO_LIMIT
        await message.reply_text(
            "👤 محدودیت تعداد کاربران این نود را وارد کنید (عدد):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_AUTO_LIMIT:
        try:
            users_limit = int(text.strip())
            if users_limit <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text(
                "❌ لطفاً یک عدد صحیح بزرگ‌تر از صفر وارد کنید.",
                reply_markup=cancel_keyboard(),
            )
            return

        panel_url = str(new_node.get("panel_url") or "").rstrip("/")
        host = urlparse(panel_url).hostname or panel_url
        title = str(new_node.get("title") or f"نود {host}")
        domain = str(new_node.get("domain") or "").strip()

        new_server_payload = {
            "title": title,
            "panel_url": panel_url,
            "admin_proxy_path": str(new_node.get("admin_proxy_path") or "").strip("/"),
            "admin_uuid": str(new_node.get("admin_uuid") or ""),
            "user_proxy_path": str(new_node.get("user_proxy_path") or "").strip("/"),
            "users_limit": int(users_limit),
            "priority": 0,
            "version": 11,
            "users": [],
            "plans": [],
            "domains": ([{"id": 1, "title": domain, "domain": domain}] if domain else []),
            "nodes": [],
        }

        try:
            await message.reply_text("⏳ در حال تست اتصال نود...")
            await hiddify_api.list_users(new_server_payload)
        except Exception as e:
            await message.reply_text(
                f"❌ اتصال به پنل نود برقرار نشد:\n{_short_error(e)}",
                reply_markup=cancel_keyboard(),
            )
            return

        try:
            created_server = database.add_server(new_server_payload)
            child_server_id = int(created_server.get("id") or 0)

            parent_nodes = _load_nodes_for_server(int(parent_server_id))
            new_node_id = max((n["id"] for n in parent_nodes), default=0) + 1
            parent_nodes.append(
                {
                    "id": new_node_id,
                    "title": title,
                    "host": host,
                    "target_server_id": child_server_id,
                }
            )
            _save_nodes_for_server(int(parent_server_id), parent_nodes)
        except Exception as e:
            logger.exception("Auto node creation failed: %s", e)
            await message.reply_text(
                f"❌ خطا در ذخیره نود خودکار:\n{_short_error(e)}",
                reply_markup=cancel_keyboard(),
            )
            return

        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_auto_node", None)

        await message.reply_text(
            f"✅ نود خودکار «{title}» با موفقیت ساخته شد.\n"
            f"🆔 سرور نود: {child_server_id}\n"
            f"🌐 آدرس: {host}",
            reply_markup=admin_main_keyboard(),
        )
        await send_nodes_menu(int(parent_server_id), message.chat_id, context)
        return

    context.user_data.pop("state", None)
    await message.reply_text(
        "❌ وضعیت ساخت خودکار نود نامعتبر است.",
        reply_markup=admin_main_keyboard(),
    )


async def handle_real_add_node_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    ساخت واقعی VPS از Provider + DNS + ثبت نود.
    """
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()

    if _is_cancel(text):
        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_real_node", None)
        await message.reply_text("❌ ساخت VPS لغو شد.", reply_markup=admin_main_keyboard())
        return

    parent_server_id = context.user_data.get("nodes_server_id")
    if parent_server_id is None:
        context.user_data.pop("state", None)
        context.user_data.pop("new_real_node", None)
        await message.reply_text(
            "❌ سرور مبدا نامشخص است. دوباره از منوی نودها اقدام کنید.",
            reply_markup=admin_main_keyboard(),
        )
        return

    new_real = context.user_data.get("new_real_node") or {}

    if state == NODES_STATE_REAL_TITLE:
        if not text:
            await message.reply_text("❌ عنوان نود خالی است.", reply_markup=cancel_keyboard())
            return
        new_real["title"] = text
        context.user_data["new_real_node"] = new_real
        context.user_data["state"] = NODES_STATE_REAL_LIMIT
        await message.reply_text(
            "👤 محدودیت کاربران نود را وارد کنید (عدد):",
            reply_markup=cancel_keyboard(),
        )
        return

    if state == NODES_STATE_REAL_LIMIT:
        try:
            users_limit = int(text)
            if users_limit <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ لطفاً عدد معتبر بزرگ‌تر از صفر وارد کنید.", reply_markup=cancel_keyboard())
            return

        title = str(new_real.get("title") or "Node")
        await message.reply_text("⏳ در حال ساخت VPS + DNS + ثبت نود. این عملیات ممکن است زمان‌بر باشد...")
        try:
            result = await node_ops.provision_and_register_node(
                parent_server_id=int(parent_server_id),
                node_title=title,
                users_limit=int(users_limit),
            )
        except Exception as e:
            logger.exception("Real node provisioning failed: %s", e)
            await message.reply_text(
                f"❌ ساخت VPS واقعی ناموفق بود:\n{_short_error(e)}",
                reply_markup=cancel_keyboard(),
            )
            return

        context.user_data.pop("state", None)
        context.user_data.pop("nodes_server_id", None)
        context.user_data.pop("new_real_node", None)

        await message.reply_text(
            "✅ VPS واقعی ساخته شد و نود ثبت شد.\n"
            f"🆔 سرور نود: {result['child_server_id']}\n"
            f"🌐 دامنه: {result['fqdn']}\n"
            f"🧭 IP: {result['ip']}\n"
            f"📡 Panel: {result['panel_url']}",
            reply_markup=admin_main_keyboard(),
        )
        await send_nodes_menu(int(parent_server_id), message.chat_id, context)
        return

    context.user_data.pop("state", None)
    await message.reply_text("❌ وضعیت ساخت VPS نامعتبر است.", reply_markup=admin_main_keyboard())


async def handle_edit_node_flow(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()

    if _is_cancel(text):
        context.user_data.pop("state", None)
        context.user_data.pop("nodes_edit_server_id", None)
        context.user_data.pop("nodes_edit_node_id", None)
        await message.reply_text("❌ ویرایش نود لغو شد.", reply_markup=admin_main_keyboard())
        return

    try:
        server_id = int(context.user_data.get("nodes_edit_server_id") or 0)
        node_id = int(context.user_data.get("nodes_edit_node_id") or 0)
    except (TypeError, ValueError):
        server_id = 0
        node_id = 0

    if server_id <= 0 or node_id <= 0:
        context.user_data.pop("state", None)
        await message.reply_text("❌ وضعیت ویرایش نود نامعتبر است.", reply_markup=admin_main_keyboard())
        return

    nodes, node, idx = _find_node(server_id, node_id)
    if not node or idx < 0:
        context.user_data.pop("state", None)
        await message.reply_text("❌ نود برای ویرایش پیدا نشد.", reply_markup=admin_main_keyboard())
        return

    target_sid = int(node.get("target_server_id") or 0)
    child = database.get_server_by_id(target_sid) if target_sid > 0 else None

    if state == NODES_STATE_EDIT_TITLE:
        if not text:
            await message.reply_text("❌ عنوان نود نمی‌تواند خالی باشد.", reply_markup=cancel_keyboard())
            return
        node["title"] = text
        nodes[idx] = node
        _save_nodes_for_server(server_id, nodes)
        if child:
            database.update_server(target_sid, {"title": text})

    elif state == NODES_STATE_EDIT_USER_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path کاربر خالی است.", reply_markup=cancel_keyboard())
            return
        if not child:
            await message.reply_text("❌ سرور نود پیدا نشد.", reply_markup=cancel_keyboard())
            return
        database.update_server(target_sid, {"user_proxy_path": val})

    elif state == NODES_STATE_EDIT_ADMIN_PROXY:
        val = text.strip().strip("/")
        if not val:
            await message.reply_text("❌ Proxy Path ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        if not child:
            await message.reply_text("❌ سرور نود پیدا نشد.", reply_markup=cancel_keyboard())
            return
        database.update_server(target_sid, {"admin_proxy_path": val})

    elif state == NODES_STATE_EDIT_ADMIN_UUID:
        val = text.strip()
        if not val:
            await message.reply_text("❌ UUID/API Key ادمین خالی است.", reply_markup=cancel_keyboard())
            return
        if not child:
            await message.reply_text("❌ سرور نود پیدا نشد.", reply_markup=cancel_keyboard())
            return
        database.update_server(target_sid, {"admin_uuid": val})

    elif state == NODES_STATE_EDIT_PANEL_URL:
        val = text.strip().rstrip("/")
        if not (val.startswith("http://") or val.startswith("https://")):
            await message.reply_text("❌ آدرس پنل باید با http/https باشد.", reply_markup=cancel_keyboard())
            return
        if not child:
            await message.reply_text("❌ سرور نود پیدا نشد.", reply_markup=cancel_keyboard())
            return
        database.update_server(target_sid, {"panel_url": val})
        node["host"] = urlparse(val).hostname or node.get("host")
        nodes[idx] = node
        _save_nodes_for_server(server_id, nodes)

    elif state == NODES_STATE_EDIT_USERS_LIMIT:
        try:
            val = int(text)
            if val <= 0:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ لطفاً عدد معتبر بزرگ‌تر از صفر وارد کنید.", reply_markup=cancel_keyboard())
            return
        if not child:
            await message.reply_text("❌ سرور نود پیدا نشد.", reply_markup=cancel_keyboard())
            return
        database.update_server(target_sid, {"users_limit": val})

    else:
        context.user_data.pop("state", None)
        await message.reply_text("❌ وضعیت ویرایش نود نامعتبر است.", reply_markup=admin_main_keyboard())
        return

    context.user_data.pop("state", None)
    context.user_data.pop("nodes_edit_server_id", None)
    context.user_data.pop("nodes_edit_node_id", None)
    await message.reply_text("✅ تغییرات نود ذخیره شد.", reply_markup=admin_main_keyboard())
    await send_node_edit_menu(server_id, node_id, message.chat_id, context)


async def handle_nodes_state_message(
    state: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    این تابع از سمت servers.py صدا زده می‌شود،
    و state های مربوط به نود را مدیریت می‌کند.
    """
    if state.startswith("nodes_add_"):
        await handle_add_node_flow(state, update, context)
        return
    if state.startswith("nodes_auto_"):
        await handle_auto_add_node_flow(state, update, context)
        return
    if state.startswith("nodes_real_"):
        await handle_real_add_node_flow(state, update, context)
        return
    if state.startswith("nodes_edit_"):
        await handle_edit_node_flow(state, update, context)
        return

    # اگر state با nodes_ شروع بشه و ناشناخته باشه:
    logger.warning("Unknown nodes state: %s", state)
    message = update.message
    if message:
        await message.reply_text(
            "❌ وضعیت نود نامعتبر است. دوباره تلاش کنید.",
            reply_markup=admin_main_keyboard(),
        )


# ===============================
#   هندلر دکمه‌های اینلاین نودها
# ===============================

async def handle_nodes_inline_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    این تابع فقط دکمه‌های مربوط به نودها را مدیریت می‌کند:
    nodes:..., delnode:..., nodeinfo:..., nodeedit:...
    """
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()
    msg = query.message
    chat_id = msg.chat_id

    # ------ نمایش منوی ویرایش نود (nodeinfo:SERVER_ID:NODE_ID) ------
    if data.startswith("nodeinfo:"):
        await query.answer()
        try:
            _, sid_str, nid_str = data.split(":", 2)
            server_id = int(sid_str)
            node_id = int(nid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه نود نامعتبر است.")
            return

        await send_node_edit_menu(server_id, node_id, chat_id, context, message=msg)
        return

    # ------ شروع ویرایش یک فیلد نود ------
    if data.startswith("nodeedit:"):
        await query.answer()
        try:
            _, sid_str, nid_str, field = data.split(":", 3)
            server_id = int(sid_str)
            node_id = int(nid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی ویرایش نود نامعتبر است.")
            return

        _, node, _ = _find_node(server_id, node_id)
        if not node:
            await msg.edit_text("❌ نود مورد نظر پیدا نشد.")
            return

        context.user_data["nodes_edit_server_id"] = server_id
        context.user_data["nodes_edit_node_id"] = node_id

        prompt = ""
        next_state = ""
        if field == "title":
            next_state = NODES_STATE_EDIT_TITLE
            prompt = "✏️ عنوان جدید نود را ارسال کنید:"
        elif field == "user_proxy":
            next_state = NODES_STATE_EDIT_USER_PROXY
            prompt = "🔐 Proxy Path کاربر جدید را ارسال کنید (بدون /):"
        elif field == "admin_proxy":
            next_state = NODES_STATE_EDIT_ADMIN_PROXY
            prompt = "🔐 Proxy Path ادمین جدید را ارسال کنید (بدون /):"
        elif field == "admin_uuid":
            next_state = NODES_STATE_EDIT_ADMIN_UUID
            prompt = "🔑 UUID/API Key ادمین جدید را ارسال کنید:"
        elif field == "panel_url":
            next_state = NODES_STATE_EDIT_PANEL_URL
            prompt = "🌐 آدرس پنل جدید را ارسال کنید (با http/https):"
        elif field == "users_limit":
            next_state = NODES_STATE_EDIT_USERS_LIMIT
            prompt = "👤 محدودیت کاربران جدید را ارسال کنید (عدد):"
        elif field == "domains":
            target_sid = int((node or {}).get("target_server_id") or 0)
            if target_sid <= 0:
                await msg.edit_text("❌ سرور نود برای مدیریت دامنه پیدا نشد.")
                return
            from AdminBot.servers import send_domains_menu  # import lazy to avoid circular import at module load
            await send_domains_menu(target_sid, chat_id, context, message=msg)
            return
        else:
            await msg.edit_text("❌ فیلد ویرایش نود نامعتبر است.")
            return

        context.user_data["state"] = next_state
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await msg.reply_text(prompt, reply_markup=cancel_keyboard())
        return

    # ------ مدیریت منوهای nodes:SERVER_ID:ACTION ------
    if data.startswith("nodes:"):
        await query.answer()
        try:
            _, sid_str, action = data.split(":", 2)
            server_id = int(sid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه نود نامعتبر است.")
            return

        # برگشت از منوی حذف به منوی اصلی نودها
        if action == "back":
            await send_nodes_menu(server_id, chat_id, context, message=msg)
            return

        # افزودن نود
        if action == "add":
            context.user_data["state"] = NODES_STATE_ADD_TITLE
            context.user_data["nodes_server_id"] = server_id
            context.user_data["new_node"] = {}

            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await msg.reply_text(
                "➕ افزودن دستی نود (با تست اتصال)\n"
                "برای نود یک عنوان وارد کنید:",
                reply_markup=cancel_keyboard(),
            )
            return

        # حذف نود
        if action == "remove":
            await send_nodes_delete_menu(server_id, chat_id, context, message=msg)
            return

        await msg.edit_text("❌ گزینه‌ی نود نامعتبر است.")
        return

    # ------ حذف نود (delnode:SERVER_ID:NODE_ID) ------
    if data.startswith("delnode:"):
        await query.answer()
        try:
            _, sid_str, nid_str = data.split(":", 2)
            server_id = int(sid_str)
            node_id = int(nid_str)
        except ValueError:
            await msg.edit_text("❌ داده‌ی دکمه حذف نود نامعتبر است.")
            return

        nodes = _load_nodes_for_server(server_id)
        new_nodes = [n for n in nodes if n["id"] != node_id]

        if len(new_nodes) == len(nodes):
            await msg.edit_text("❌ نود مورد نظر پیدا نشد یا قبلاً حذف شده است.")
            return

        # شناسه‌ها را مرتب می‌کنیم که مرتب بماند
        for idx, n in enumerate(new_nodes, start=1):
            n["id"] = idx

        try:
            _save_nodes_for_server(server_id, new_nodes)
        except Exception as e:
            logger.exception("Error deleting node: %s", e)
            await msg.edit_text(f"❌ خطا در حذف نود:\n{_short_error(e)}")
            return

        await msg.edit_text("✅ نود با موفقیت حذف شد.")
        await send_nodes_menu(server_id, chat_id, context)
        return
