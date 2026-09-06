from typing import Any, Dict, List, Optional

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup
from Shared import i18n
from Shared.tg_button_styles import keyboard_button as KButton
from Shared.tg_button_styles import inline_button as IButton


BTN_SUBSCRIPTIONS = "\U0001f4ca \u0645\u062f\u06cc\u0631\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u0647\u0627"
BTN_WALLET = "\U0001f4b0 \u06a9\u06cc\u0641 \u067e\u0648\u0644"
BTN_PLANS = "\U0001f4b5 \u067e\u0644\u0646\u200c\u0647\u0627"
BTN_CUSTOMER_BOT = "\U0001f916 \u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc"
BTN_TICKETS = "\U0001f3ab \u0645\u062f\u06cc\u0631\u06cc\u062a \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627"
BTN_SETTINGS = "\u2699\ufe0f \u0645\u062f\u06cc\u0631\u06cc\u062a \u0631\u0628\u0627\u062a"
BTN_BACK = "\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a"

# کلیدهای i18n منوی اصلی نماینده (برای مچر چندزبانه)
AGENT_MENU_KEYS = (
    "ag_menu_subscriptions", "ag_menu_wallet", "ag_menu_plans",
    "ag_menu_customer_bot", "ag_menu_tickets", "ag_menu_settings",
)


def _ikb(rows: List[List[Any]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard(lang: str = "fa"):
    kb = [
        [KButton(i18n.t("ag_menu_subscriptions", lang))],
        [KButton(i18n.t("ag_menu_plans", lang)), KButton(i18n.t("ag_menu_wallet", lang))],
        [KButton(i18n.t("ag_menu_customer_bot", lang))],
        [KButton(i18n.t("ag_menu_tickets", lang)), KButton(i18n.t("ag_menu_settings", lang))],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def agent_lang(context) -> str:
    """زبان ذخیره‌شده نماینده از context."""
    try:
        from AgentBot.handlers.base import get_agent_id
        return i18n.get_agent_lang(int(get_agent_id(context) or 0))
    except Exception:
        return "fa"


def language_keyboard( lang: str = "fa"):
    rows = []
    langs = i18n.supported_langs()
    for i in range(0, len(langs), 2):
        rows.append([IButton(i18n.lang_display_name(lg), callback_data=f"lang:set:{lg}") for lg in langs[i:i + 2]])
    return _ikb(rows)


def back_keyboard(callback_data: str = f"agbot:menu", lang: str = "fa") -> InlineKeyboardMarkup:
    return _ikb([[IButton(i18n.t("back", lang), callback_data=callback_data)]])


def cancel_keyboard( lang: str = "fa"):
    _lg = lang
    return ReplyKeyboardMarkup(
        [[KButton(i18n.t('❌ لغو', _lg), style="danger")]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def rename_cancel_keyboard( lang: str = "fa"):
    """کیبورد پایین برای حالت تغییر نام — دکمه بازگشت در کیبورد اصلی"""
    _lg = lang
    return ReplyKeyboardMarkup(
        [[KButton(i18n.t("back", _lg))], [KButton(i18n.t('❌ لغو', _lg))]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def send_msg_keyboard( lang: str = "fa"):
    _lg = lang
    return ReplyKeyboardMarkup(
        [[KButton(i18n.t('◀️ بازگشت', _lg), style="danger")]],
        resize_keyboard=True,
    )


def broadcast_skip_cancel_keyboard( lang: str = "fa"):
    _lg = lang
    return ReplyKeyboardMarkup(
        [
            [KButton(i18n.t('⏩رد کردن', _lg))],
            [KButton(i18n.t('❌ لغو', _lg))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# Subscription keyboards
def subs_menu_keyboard(lang: str = "fa"):
    return _ikb([
        [IButton(i18n.t("ag_subs_users_list", lang), callback_data="agbot:subs:list:1")],
        [IButton(i18n.t("ag_subs_create", lang), callback_data="agbot:subs:create")],
        [IButton(i18n.t("ag_subs_search", lang), callback_data="agbot:subs:search")],
        [IButton(i18n.t("ag_subs_expired", lang), callback_data="agbot:subs:expired")],
        [IButton(i18n.t("back", lang), callback_data="agbot:menu")],
    ])


def subs_search_keyboard(lang: str = "fa") -> InlineKeyboardMarkup:
    """زیرمنوی جستجو: جستجو با نام / جستجو با شناسه."""
    return _ikb([
        [IButton(i18n.t("ag_search_by_name", lang), callback_data="agbot:subs:searchname")],
        [IButton(i18n.t("ag_search_by_id", lang), callback_data="agbot:subs:searchid")],
        [IButton(i18n.t("back", lang), callback_data="agbot:subs:back")],
    ])


def service_detail_keyboard(service_id: int, is_active: bool, lang: str = "fa"):
    rows = [[IButton(i18n.t("ag_svc_get_config", lang), callback_data=f"agbot:subs:cfg:{service_id}")]]
    if is_active:
        rows.append([IButton(i18n.t("ag_svc_renew", lang), callback_data=f"agbot:subs:renew:{service_id}")])
        rows.append([IButton(i18n.t("ag_svc_rename", lang), callback_data=f"agbot:subs:rename:{service_id}")])
        rows.append([IButton(i18n.t("ag_svc_disable", lang), callback_data=f"agbot:subs:disable:{service_id}")])
    else:
        rows.append([IButton(i18n.t("ag_svc_enable", lang), callback_data=f"agbot:subs:enable:{service_id}")])
        rows.append([IButton(i18n.t("ag_svc_rename", lang), callback_data=f"agbot:subs:rename:{service_id}")])
    rows.append([IButton(i18n.t("ag_svc_newlink", lang), callback_data=f"agbot:subs:newlink:{service_id}")])
    rows.append([IButton(i18n.t("ag_svc_delete", lang), callback_data=f"agbot:subs:delete:{service_id}")])
    rows.append([IButton(i18n.t("back", lang), callback_data="agbot:subs:back")])
    return _ikb(rows)


def subs_configs_keyboard(service_id: int, *, show_direct_config: bool = True,
                          show_sub_link: bool = True, show_auto_sub_link: bool = False,
                          show_sub_link_b64: bool = False, show_multi_server: bool = False,
                          show_multi_server_b64: bool = False, lang: str = "fa") -> InlineKeyboardMarkup:
    """زیرمنوی «دریافت کانفیگ»: فقط دکمه‌های لینکی که ادمین فعال کرده (مثل ربات مشتری)."""
    _lg = lang
    rows = []
    if show_direct_config:
        rows.append([IButton(i18n.t('⚔️ کانفیگ مستقیم', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:direct")])
    if show_sub_link:
        rows.append([IButton(i18n.t('🔗 لینک اشتراک', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:sub_link")])
    if show_auto_sub_link:
        rows.append([IButton(i18n.t('🤖 اشتراک خودکار', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:auto_sub")])
    if show_sub_link_b64:
        rows.append([IButton(i18n.t('🔐 لینک اشتراک b64', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:sub_b64")])
    if show_multi_server:
        rows.append([IButton(i18n.t('🌐 لینک اشتراک هوشمند', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:multi")])
    if show_multi_server_b64:
        rows.append([IButton(i18n.t('🌐 لینک اشتراک هوشمند b64', _lg), callback_data=f"agbot:subs:cfgmenu:{service_id}:multi_b64")])
    rows.append([IButton(i18n.t("back", _lg), callback_data=f"agbot:subs:detail:{service_id}")])
    return _ikb(rows)


# Wallet keyboards
def wallet_menu_keyboard(lang: str = "fa"):
    return _ikb([
        [IButton(i18n.t("ag_wallet_card", lang), callback_data="agbot:wallet:charge")],
        [IButton(i18n.t("back", lang), callback_data="agbot:menu")],
    ])


# Plans keyboards
def plans_menu_keyboard(current_mode: str = "dynamic", lang: str = "fa"):
    mode = str(current_mode or "dynamic").strip().lower()
    if mode == "fixed":
        settings_button = IButton(i18n.t("ag_plans_set_fixed", lang), callback_data="agbot:plans:fixed")
    else:
        settings_button = IButton(i18n.t("ag_plans_set_dyn", lang), callback_data="agbot:plans:dynset")
    return _ikb([
        [IButton(i18n.t("ag_plans_display", lang), callback_data="agbot:plans:mode")],
        [settings_button],
        [IButton(i18n.t("ag_plans_discount", lang), callback_data="agbot:plans:discount")],
        [IButton(i18n.t("back", lang), callback_data="agbot:menu")],
    ])


def plans_mode_keyboard(current_mode: str, lang: str = "fa"):
    dyn = (i18n.t("ag_state_on", lang) if current_mode == "dynamic" else i18n.t("ag_state_off", lang)) + " " + i18n.t("ag_plans_dyn", lang)
    fix = (i18n.t("ag_state_on", lang) if current_mode == "fixed" else i18n.t("ag_state_off", lang)) + " " + i18n.t("ag_plans_fixed", lang)
    return _ikb([
        [IButton(dyn, callback_data="agbot:plans:mode:toggle:dynamic")],
        [IButton(fix, callback_data="agbot:plans:mode:toggle:fixed")],
        [IButton(i18n.t("back", lang), callback_data="agbot:plans:back")],
    ])


# Customer bot keyboards
def cbot_menu_keyboard(bot_active: bool, lang: str = "fa"):
    toggle = (i18n.t("ag_state_on", lang) if not bot_active else i18n.t("ag_state_off", lang)) + " " + (i18n.t("ag_activate", lang) if not bot_active else i18n.t("ag_deactivate", lang))
    rows = [
        [IButton(toggle, callback_data="agbot:cbot:activate")],
        [IButton(i18n.t("ag_cbot_token", lang), callback_data="agbot:cbot:token")],
        [IButton(i18n.t("ag_cbot_restart", lang), callback_data="agbot:cbot:restart")],
        [IButton(i18n.t("back", lang), callback_data="agbot:menu")],
    ]
    return _ikb(rows)


# Ticket keyboards
def tickets_menu_keyboard(lang: str = "fa"):
    return _ikb([
        [IButton(i18n.t("ag_tickets_pending", lang), callback_data="agbot:ticket:pending")],
        [IButton(i18n.t("ag_tickets_open", lang), callback_data="agbot:ticket:open")],
        [IButton(i18n.t("ag_tickets_closed", lang), callback_data="agbot:ticket:closed")],
        [IButton(i18n.t("back", lang), callback_data="agbot:menu")],
    ])


def ticket_detail_keyboard(ticket_id: int, status: str, lang: str = "fa"):
    rows = []
    if status in ("open", "pending"):
        rows.append([IButton(i18n.t("ag_ticket_reply", lang), callback_data=f"agbot:ticket:reply:{ticket_id}")])
        rows.append([IButton(i18n.t("ag_ticket_close", lang), callback_data=f"agbot:ticket:close:{ticket_id}")])
    elif status == "closed":
        # مثل ربات ادمین: امکان باز کردن دوباره تیکت بسته
        rows.append([IButton(i18n.t("ag_ticket_reopen", lang), callback_data=f"agbot:ticket:reopen:{ticket_id}")])
    rows.append([IButton(i18n.t("back", lang), callback_data="agbot:ticket:back")])
    return _ikb(rows)


def ticket_reply_skip_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('▶️ رد کردن', _lg), callback_data="agbot:ticket:replyshot:skip")],
        [IButton(i18n.t('❌ لغو', _lg), callback_data="agbot:ticket:replyshot:cancel")],
    ])


def ticket_reply_confirm_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [
            IButton(i18n.t('✅ ارسال', _lg), callback_data="agbot:ticket:replyconfirm:send"),
            IButton(i18n.t('✏️ ویرایش', _lg), callback_data="agbot:ticket:replyconfirm:edit"),
        ],
        [IButton(i18n.t('❌ لغو', _lg), callback_data="agbot:ticket:replyconfirm:cancel")],
    ])


# Settings root keyboard
def settings_menu_keyboard(lang: str = "fa"):
    return _ikb([
        [IButton(i18n.t("ag_set_users", lang), callback_data="agbot:set:users")],
        [
            IButton(i18n.t("ag_set_orders", lang), callback_data="agbot:set:orders"),
            IButton(i18n.t("ag_set_tx", lang), callback_data="agbot:set:tx"),
        ],
        [
            IButton(i18n.t("ag_set_gifts", lang), callback_data="agbot:set:gifts"),
            IButton(i18n.t("ag_set_broadcast", lang), callback_data="agbot:set:broadcast"),
        ],
        [IButton(i18n.t("ag_set_config", lang), callback_data="agbot:set:config")],
    ])


def broadcast_menu_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('تمام کاربران', _lg), callback_data="agbot:broadcast:segment:all")],
        [IButton(i18n.t('تمام کاربران منقضی شده', _lg), callback_data="agbot:broadcast:segment:expired_all")],
        [IButton(i18n.t('کاربران بدون سفارش', _lg), callback_data="agbot:broadcast:segment:no_order")],
        [IButton(i18n.t('کاربران منقضی شده بیش از یک هفته', _lg), callback_data="agbot:broadcast:segment:expired_1w")],
        [IButton(i18n.t('کاربران منقضی شده بیش از دو هفته', _lg), callback_data="agbot:broadcast:segment:expired_2w")],
        [IButton(i18n.t('کاربران منقضی شده بیش از چهار هفته', _lg), callback_data="agbot:broadcast:segment:expired_4w")],
        [IButton(i18n.t('کاربران منقضی شده بیش از هشت هفته', _lg), callback_data="agbot:broadcast:segment:expired_8w")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:back")],
    ])


def users_profile_keyboard(customer_id: int, telegram_id: int, back_callback: str = "agbot:set:users", lang: str = "fa"):
    """کیبورد پروفایل کاربر برای نماینده"""
    _lg = lang
    rows = [
        [IButton(i18n.t('📋 لیست سرویس‌ها', _lg), callback_data=f"agbot:set:users:services:{customer_id}")],
        [IButton(i18n.t('📗 لیست سفارشات', _lg), callback_data=f"agbot:set:users:orders:{customer_id}")],
        [IButton(i18n.t('💵 لیست تراکنش‌ها', _lg), callback_data=f"agbot:set:users:tx:{customer_id}")],
        [IButton(i18n.t('🚫 مسدود/آزادسازی کاربر', _lg), callback_data=f"agbot:set:users:ban:{customer_id}")],
        [IButton(i18n.t('📑 لیست تیکت‌ها', _lg), callback_data=f"agbot:set:users:tickets:{customer_id}")],
        [IButton(i18n.t('📨 ارسال پیام', _lg), callback_data=f"agbot:set:users:message:{telegram_id}")],
        [IButton(i18n.t("back", _lg), callback_data=back_callback)],
    ]
    return _ikb(rows)


def settings_sub_menu_keyboard(prefix: str, list_label: Optional[str] = None, lang: str = "fa"):
    _lg = lang
    if list_label is None:
        list_label = i18n.t('📋 لیست', _lg)
    return _ikb([
        [IButton(list_label, callback_data=f"agbot:{prefix}:list")],
        [IButton(i18n.t('🔍 جستجو', _lg), callback_data=f"agbot:{prefix}:search")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:back")],
    ])


def orders_menu_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('📗لیست سفارشات', _lg), callback_data="agbot:set:orders:list")],
        [IButton(i18n.t('🔍جستجوی سفارشات', _lg), callback_data="agbot:set:orders:search")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:back")],
    ])


def orders_list_keyboard(orders: List[Dict[str, Any]], page: int, total_pages: int, back_callback: str = "agbot:set:orders", lang: str = "fa"):
    """کیبورد لیست سفارشات — گرید ۳ ستونه شناسه سفارش + صفحه‌بندی."""
    _lg = lang
    rows = []
    current_row = []
    for o in orders:
        oid = str(o.get("id"))
        current_row.append(IButton(oid, callback_data=f"agbot:set:orders:detail:{oid}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    nav_row = []
    if page > 1:
        nav_row.append(IButton("\u27a1\ufe0f", callback_data=f"agbot:set:orders:list:{page - 1}"))
    nav_row.append(IButton(f"{page}/{total_pages}", callback_data="agbot:set:orders:noop"))
    if page < total_pages:
        nav_row.append(IButton("\u2b05\ufe0f", callback_data=f"agbot:set:orders:list:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([IButton(i18n.t('🔙بازگشت', _lg), callback_data=back_callback)])
    return _ikb(rows)


def order_search_results_keyboard(orders: List[Dict[str, Any]], back_callback: str = "agbot:set:orders", lang: str = "fa"):
    """کیبورد نتایج جستجوی سفارش — دکمه‌های شناسه سفارش + بازگشت."""
    _lg = lang
    rows = []
    current_row = []
    for o in orders:
        current_row.append(IButton(str(o.get("id")), callback_data=f"agbot:set:orders:detail:{o.get('id')}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([IButton(i18n.t('🔙بازگشت', _lg), callback_data=back_callback)])
    return _ikb(rows)


def tx_menu_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('✅لیست تراکنشات تایید شده', _lg), callback_data="agbot:set:tx:approved")],
        [IButton(i18n.t('❌لیست تراکنشات رد شده', _lg), callback_data="agbot:set:tx:rejected")],
        [IButton(i18n.t('⏳لیست تراکنشات در انتظار', _lg), callback_data="agbot:set:tx:pending")],
        [IButton(i18n.t('💳لیست تراکنشات کارت به کارت', _lg), callback_data="agbot:set:tx:card")],
        [IButton(i18n.t('🔍جستجوی تراکنش', _lg), callback_data="agbot:set:tx:search")],
        [IButton(i18n.t('🔙بازگشت', _lg), callback_data="agbot:set:back")],
    ])


def config_menu_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('🛒 تنظیمات خرید و تمدید', _lg), callback_data="agbot:set:cfg:shop")],
        [IButton(i18n.t('💳 تنظیمات پرداخت', _lg), callback_data="agbot:set:cfg:payment")],
        [IButton(i18n.t('🔒 تنظیمات عضویت اجباری', _lg), callback_data="agbot:set:cfg:forcejoin")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:back")],
    ])


def shop_settings_keyboard(buy_enabled: bool, renew_enabled: bool, lang: str = "fa"):
    _lg = lang
    buy = i18n.t('✅ امکان خرید اشتراک', _lg) if buy_enabled else i18n.t('❌ امکان خرید اشتراک', _lg)
    renew = i18n.t('✅ امکان تمدید اشتراک', _lg) if renew_enabled else i18n.t('❌ امکان تمدید اشتراک', _lg)
    return _ikb([
        [IButton(buy, callback_data="agbot:shop:buy")],
        [IButton(renew, callback_data="agbot:shop:renew")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:cfg:back")],
    ])


def payment_settings_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('💳 تنظیمات کارت به کارت', _lg), callback_data="agbot:pay:menu")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:cfg:back")],
    ])


def card_settings_keyboard(card_enabled: bool, last4: bool, rand_tx: bool, sms_auto: bool, lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('✅ پرداخت کارت به کارت', _lg) if card_enabled else i18n.t('❌ پرداخت کارت به کارت', _lg), callback_data="agbot:pay:card")],
        [IButton(i18n.t('✅ الزام 4 رقم آخر کارت', _lg) if last4 else i18n.t('❌ الزام 4 رقم آخر کارت', _lg), callback_data="agbot:pay:last4")],
        [IButton(i18n.t('✅ مشخصه تصادفی تراکنش', _lg) if rand_tx else i18n.t('❌ مشخصه تصادفی تراکنش', _lg), callback_data="agbot:pay:randtx")],
        [IButton(i18n.t('🤖 تایید خودکار SMS بانک', _lg), callback_data="agbot:pay:smsauto")],
        [IButton(i18n.t('💳 لیست کارت‌ها', _lg), callback_data="agbot:pay:cards")],
        [IButton(i18n.t('✏️ تنظیم متن کارت به کارت', _lg), callback_data="agbot:pay:cardtext")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:set:cfg:back")],
    ])


def payment_cards_list_keyboard(cards, lang: str = "fa"):
    _lg = lang
    rows = []
    for c in cards:
        number = str(c.get("card_number") or "").strip()
        if not number:
            continue
        bank = str(c.get("bank_name") or "").strip()
        label = f"{bank} - {number}" if bank else number
        rows.append([IButton(label, callback_data=f"agbot:pay:cardedit:{c['id']}")])
    rows.append([IButton(i18n.t('➕ افزودن کارت', _lg), callback_data="agbot:pay:cardadd")])
    rows.append([IButton(i18n.t("back", _lg), callback_data="agbot:pay:menu")])
    return _ikb(rows)


def sms_webhook_settings_keyboard(enabled: bool, lang: str = "fa"):
    _lg = lang
    enabled_icon = "✅" if enabled else "❌"
    return _ikb([
        [IButton(f"{i18n.t('تایید خودکار SMS | ', _lg)}{enabled_icon}", callback_data="agbot:pay:smsauto:toggle")],
        [IButton(i18n.t('🔑 ساخت / تعویض Secret', _lg), callback_data="agbot:pay:smsauto:regen")],
        [IButton(i18n.t('👁 نمایش Secret برای اپ', _lg), callback_data="agbot:pay:smsauto:show")],
        [IButton(i18n.t('📱 راهنمای اتصال اپ', _lg), callback_data="agbot:pay:smsauto:help")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:pay:menu")],
    ])


def pagination_keyboard(base_callback: str, page: int, total_pages: int, back_callback: str, lang: str = "fa"):
    _lg = lang
    rows = []
    nav = []
    if page > 1:
        nav.append(IButton(i18n.t('⬅️ قبلی', _lg), callback_data=f"{base_callback}:{page - 1}"))
    if page < total_pages:
        nav.append(IButton(i18n.t('بعدی ➡️', _lg), callback_data=f"{base_callback}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([IButton(i18n.t("back", _lg), callback_data=back_callback)])
    return _ikb(rows)


def tx_list_keyboard(payments: List[Dict[str, Any]], filter_type: str, page: int, total_pages: int, back_callback: str = "agbot:set:tx", lang: str = "fa"):
    _lg = lang
    rows = []
    current_row = []
    for p in payments:
        current_row.append(IButton(str(p.get("id")), callback_data=f"agbot:set:tx:detail:{p.get('id')}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    nav_row = []
    if page > 1:
        nav_row.append(IButton("\u27a1\ufe0f", callback_data=f"agbot:set:tx:{filter_type}:{page - 1}"))
    nav_row.append(IButton(f"{page}/{total_pages}", callback_data="agbot:set:tx:noop"))
    if page < total_pages:
        nav_row.append(IButton("\u2b05\ufe0f", callback_data=f"agbot:set:tx:{filter_type}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([IButton(i18n.t('🔙بازگشت', _lg), callback_data=back_callback)])
    return _ikb(rows)


def tx_search_results_keyboard(payments: List[Dict[str, Any]], back_callback: str = "agbot:set:tx", lang: str = "fa"):
    """کیبورد نتایج جستجوی تراکنش — دکمه‌های شناسه تراکنش + بازگشت."""
    _lg = lang
    rows = []
    current_row = []
    for p in payments:
        current_row.append(IButton(str(p.get("id")), callback_data=f"agbot:set:tx:detail:{p.get('id')}"))
        if len(current_row) == 3:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([IButton(i18n.t('🔙بازگشت', _lg), callback_data=back_callback)])
    return _ikb(rows)


# Fixed plan keyboards
def plans_cats_keyboard(cats, lang: str = "fa"):
    _lg = lang
    rows = []
    for c in cats:
        count = int(c.get('plan_count', 0) or 0)
        rows.append([IButton(f"\U0001f6d2 {c['title']}  \u2022  {count}{i18n.t(' پلن', _lg)}", callback_data=f"agbot:plans:fixed:cat:{c['id']}")])
    rows.append([IButton(i18n.t('➕ افزودن دسته جدید', _lg), callback_data='agbot:plans:fixed:cat_add')])
    rows.append([IButton(i18n.t("back", _lg), callback_data='agbot:plans:back')])
    return _ikb(rows)


def plans_cat_detail_keyboard(cat_id: int, lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('➕ افزودن پلن جدید', _lg), callback_data=f'agbot:plans:fixed:plan_add:{cat_id}')],
        [IButton(i18n.t('📋 مشاهده و مدیریت پلن‌ها', _lg), callback_data=f'agbot:plans:fixed:plans:{cat_id}')],
        [IButton(i18n.t('✏️ ویرایش عنوان دسته', _lg), callback_data=f'agbot:plans:fixed:cat_edit:{cat_id}')],
        [IButton(i18n.t('🗑 حذف دسته', _lg), callback_data=f'agbot:plans:fixed:cat_del_ask:{cat_id}')],
        [IButton(i18n.t("back", _lg), callback_data='agbot:plans:fixed')],
    ])


def plans_cat_del_confirm_keyboard(cat_id: int, lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('⚠️ تایید حذف دسته', _lg), callback_data=f'agbot:plans:fixed:cat_del:{cat_id}')],
        [IButton(i18n.t('❌ انصراف', _lg), callback_data=f'agbot:plans:fixed:cat:{cat_id}')],
    ])


def plans_cat_del_keyboard(cats, lang: str = "fa"):
    rows = []
    for c in cats:
        rows.append([IButton(f"\U0001f5d1 {c['title']}", callback_data=f"agbot:plans:fixed:cat_del:{c['id']}")])
    rows.append([IButton(i18n.t("back", lang), callback_data='agbot:plans:fixed')])
    return _ikb(rows)


def plans_plans_keyboard(plans, cat_id, lang: str = "fa"):
    _lg = lang
    rows = []
    for p in plans:
        try:
            gb_val = float(p.get('gb') or 0)
        except (TypeError, ValueError):
            gb_val = 0.0
        vol_txt = i18n.t('نامحدود', _lg) if gb_val == 0 else f'{gb_val:g}{i18n.t(' گیگ', _lg)}'
        days_val = int(p.get('days') or 0)
        days_txt = i18n.t('نامحدود', _lg) if days_val == 0 else f'{days_val}{i18n.t(' روز', _lg)}'
        label = f"\U0001f4e6 {p['title']} | {vol_txt} | {days_txt} | {int(p['price']):,}{i18n.t(' ت', _lg)}"
        rows.append([IButton(label, callback_data=f"agbot:plans:fixed:plan:{p['id']}")])
    rows.append([IButton(i18n.t('➕ افزودن پلن', _lg), callback_data=f"agbot:plans:fixed:plan_add:{cat_id}")])
    rows.append([IButton(i18n.t('🗑 حذف پلن', _lg), callback_data=f"agbot:plans:fixed:plan_del_menu:{cat_id}")])
    rows.append([IButton(i18n.t("back", _lg), callback_data=f"agbot:plans:fixed:cat:{cat_id}")])
    return _ikb(rows)


def plans_plan_del_keyboard(plans, cid, lang: str = "fa"):
    rows = []
    for p in plans:
        rows.append([IButton(f"\U0001f5d1 {p['title']}", callback_data=f"agbot:plans:fixed:plan_del:{p['id']}")])
    rows.append([IButton(i18n.t("back", lang), callback_data=f"agbot:plans:fixed:plans:{cid}")])
    return _ikb(rows)


# Dynamic plan settings keyboard
def dyn_settings_keyboard( lang: str = "fa"):
    _lg = lang
    return _ikb([
        [IButton(i18n.t('💰 قیمت هر گیگ', _lg), callback_data='agbot:plans:dyn_edit:price_per_gb')],
        [IButton(i18n.t('💰 قیمت هر ماه', _lg), callback_data='agbot:plans:dyn_edit:price_per_month')],
        [IButton(i18n.t('📊 محدوده حجم', _lg), callback_data='agbot:plans:dyn_edit:volume_range')],
        [IButton(i18n.t('⏰ محدوده زمان', _lg), callback_data='agbot:plans:dyn_edit:time_range')],
        [IButton(i18n.t('🎟 مدیریت حرفه‌ای تخفیف‌ها', _lg), callback_data='agbot:plans:discount')],
        [IButton(i18n.t("back", _lg), callback_data='agbot:plans:back')],
    ])


def discount_settings_keyboard(simple_enabled=False, tiered_enabled=False, lang: str = "fa"):
    _lg = lang
    simple_style = "danger" if simple_enabled else "success"
    tiered_style = "danger" if tiered_enabled else "success"
    return _ikb([
        [IButton(i18n.t('💎 حجمی ساده', _lg), callback_data='agbot:plans:discount:toggle:simple', style=simple_style)],
        [IButton(i18n.t('🎩 پلکانی', _lg), callback_data='agbot:plans:discount:toggle:tiers', style=tiered_style)],
        [IButton(i18n.t('✏️ ویرایش تخفیف حجمی ساده', _lg), callback_data='agbot:plans:discount:edit:simple')],
        [IButton(i18n.t('✏️ ویرایش تخفیف پلکانی', _lg), callback_data='agbot:plans:discount:edit:tiers')],
        [IButton(i18n.t('⏱️ تایمر تخفیف حجمی ساده', _lg), callback_data='agbot:plans:discount:edit:timer')],
        [IButton(i18n.t("back", _lg), callback_data='agbot:plans:dynset')],
    ])


def agent_dynamic_wizard_keyboard(server_id: int, gb: int, months: int, price: int, off_percent: int = 0, wholesale: int = 0, lang: str = "fa"):
    _lg = lang
    price_str = f"{price:,}"
    wholesale_str = f"{wholesale:,}"
    rows = [
        [IButton(i18n.t('📊 حجم', _lg), callback_data="noop")],
        [
            IButton("\u2796 10", callback_data=f"agbot:subs:wiz:gb_dec10:{server_id}"),
            IButton("\u2796", callback_data=f"agbot:subs:wiz:gb_dec:{server_id}"),
            IButton("\u2795", callback_data=f"agbot:subs:wiz:gb_inc:{server_id}"),
            IButton("\u2795 10", callback_data=f"agbot:subs:wiz:gb_inc10:{server_id}"),
        ],
        [IButton(f"{gb}{i18n.t(' گیگابایت', _lg)}", callback_data="noop")],
        [IButton(i18n.t('⏳ زمان', _lg), callback_data="noop")],
        [
            IButton("\u2796", callback_data=f"agbot:subs:wiz:month_dec:{server_id}"),
            IButton(f"{months}{i18n.t(' ماهه', _lg)}", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:wiz:month_inc:{server_id}"),
        ],
        [
            IButton(f"{i18n.t('💰 قیمت عمده: ', _lg)}{wholesale_str}{i18n.t(' تومان', _lg)}", callback_data="noop"),
        ],
        [
            IButton(f"{i18n.t('💸 قیمت فروش: ', _lg)}{price_str}{i18n.t(' تومان', _lg)}", callback_data="noop"),
            IButton(f"{i18n.t('🏷 تخفیف: ', _lg)}{off_percent}%", callback_data="noop"),
        ],
        [IButton(i18n.t('💳 تایید و ساختن', _lg), callback_data=f"agbot:subs:wiz:confirm:{server_id}")],
        [IButton(i18n.t("back", _lg), callback_data="agbot:subs:picksrv_back")],
    ]
    return _ikb(rows)


def agent_renew_wizard_keyboard(service_id: int, gb: int, months: int, price: int, off_percent: int = 0, wholesale: int = 0, lang: str = "fa"):
    """ویزارد تمدید اشتراک — دقیقاً مثل پنل ساخت اشتراک ولی برای تمدید."""
    _lg = lang
    price_str = f"{price:,}"
    wholesale_str = f"{wholesale:,}"
    rows = [
        [IButton(i18n.t('📊 حجم تمدید', _lg), callback_data="noop")],
        [
            IButton("\u2796 10", callback_data=f"agbot:subs:rewiz:gb_dec10:{service_id}"),
            IButton("\u2796", callback_data=f"agbot:subs:rewiz:gb_dec:{service_id}"),
            IButton("\u2795", callback_data=f"agbot:subs:rewiz:gb_inc:{service_id}"),
            IButton("\u2795 10", callback_data=f"agbot:subs:rewiz:gb_inc10:{service_id}"),
        ],
        [IButton(f"{gb}{i18n.t(' گیگابایت', _lg)}", callback_data="noop")],
        [IButton(i18n.t('⏳ مدت تمدید', _lg), callback_data="noop")],
        [
            IButton("\u2796", callback_data=f"agbot:subs:rewiz:month_dec:{service_id}"),
            IButton(f"{months}{i18n.t(' ماه', _lg)}", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:rewiz:month_inc:{service_id}"),
        ],
        [
            IButton(f"{i18n.t('💰 کسر از کیف پول: ', _lg)}{wholesale_str}{i18n.t(' تومان', _lg)}", callback_data="noop"),
        ],
        [
            IButton(f"{i18n.t('💸 قیمت فروش: ', _lg)}{price_str}{i18n.t(' تومان', _lg)}", callback_data="noop"),
            IButton(f"{i18n.t('🏷 تخفیف: ', _lg)}{off_percent}%", callback_data="noop"),
        ],
        [IButton(i18n.t('✅ تایید و تمدید', _lg), callback_data=f"agbot:subs:rewiz:confirm:{service_id}")],
        [IButton(i18n.t("back", _lg), callback_data=f"agbot:subs:detail:{service_id}")],
    ]
    return _ikb(rows)
