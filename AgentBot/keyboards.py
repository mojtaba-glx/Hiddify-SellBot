from typing import Any, List, Optional

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup
from Shared.tg_button_styles import keyboard_button as KButton
from Shared.tg_button_styles import inline_button as IButton


BTN_SUBSCRIPTIONS = "\U0001f4ca \u0645\u062f\u06cc\u0631\u06cc\u062a \u0627\u0634\u062a\u0631\u0627\u06a9\u200c\u0647\u0627"
BTN_WALLET = "\U0001f4b0 \u06a9\u06cc\u0641 \u067e\u0648\u0644"
BTN_PLANS = "\U0001f4b5 \u067e\u0644\u0646\u200c\u0647\u0627"
BTN_CUSTOMER_BOT = "\U0001f916 \u0631\u0628\u0627\u062a \u0645\u0634\u062a\u0631\u06cc"
BTN_TICKETS = "\U0001f3ab \u0645\u062f\u06cc\u0631\u06cc\u062a \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627"
BTN_SETTINGS = "\u2699\ufe0f \u0645\u062f\u06cc\u0631\u06cc\u062a \u0631\u0628\u0627\u062a"
BTN_BACK = "\U0001f519 \u0628\u0627\u0632\u06af\u0634\u062a"


def _ikb(rows: List[List[Any]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard():
    kb = [
        [KButton(BTN_SUBSCRIPTIONS)],
        [KButton(BTN_PLANS), KButton(BTN_WALLET)],
        [KButton(BTN_CUSTOMER_BOT)],
        [KButton(BTN_TICKETS), KButton(BTN_SETTINGS)],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def back_keyboard(callback_data: str = f"agbot:menu") -> InlineKeyboardMarkup:
    return _ikb([[IButton(BTN_BACK, callback_data=callback_data)]])


def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KButton("\u274c \u0644\u063a\u0648")]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def broadcast_skip_cancel_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KButton("⏩رد کردن")],
            [KButton("\u274c \u0644\u063a\u0648")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# Subscription keyboards
def subs_menu_keyboard():
    return _ikb([
        [IButton("\U0001f465 \u0644\u06cc\u0633\u062a \u06a9\u0627\u0631\u0628\u0631\u0627\u0646", callback_data="agbot:subs:list:1")],
        [IButton("\u2795 \u0633\u0627\u062e\u062a\u0646 \u0627\u0634\u062a\u0631\u0627\u06a9", callback_data="agbot:subs:create")],
        [IButton("\U0001f50d \u062c\u0633\u062a\u062c\u0648", callback_data="agbot:subs:search")],
        [IButton("\U0001f51c \u06a9\u0627\u0631\u0628\u0631\u0627\u0646 \u0645\u0646\u0642\u0636\u06cc \u0634\u062f\u0647", callback_data="agbot:subs:expired")],
        [IButton(BTN_BACK, callback_data="agbot:menu")],
    ])


def subs_search_keyboard() -> InlineKeyboardMarkup:
    """زیرمنوی جستجو: جستجو با نام / جستجو با شناسه."""
    return _ikb([
        [IButton("\U0001f464 \u062c\u0633\u062a\u062c\u0648 \u0628\u0627 \u0646\u0627\u0645", callback_data="agbot:subs:searchname")],
        [IButton("\U0001f511 \u062c\u0633\u062a\u062c\u0648 \u0628\u0627 \u0634\u0646\u0627\u0633\u0647", callback_data="agbot:subs:searchid")],
        [IButton(BTN_BACK, callback_data="agbot:subs:back")],
    ])


def service_detail_keyboard(service_id: int, is_active: bool):
    rows = [[IButton("\U0001f4e1 \u062f\u0631\u06cc\u0627\u0641\u062a \u06a9\u0627\u0646\u0641\u06cc\u06af", callback_data=f"agbot:subs:cfg:{service_id}")]]
    if is_active:
        rows.append([IButton("\u23f3 \u062a\u0645\u062f\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9", callback_data=f"agbot:subs:renew:{service_id}")])
        rows.append([IButton("\u274c \u063a\u06cc\u0631\u0641\u0639\u0627\u0644 \u06a9\u0631\u062f\u0646", callback_data=f"agbot:subs:disable:{service_id}")])
    else:
        rows.append([IButton("\u2705 \u0641\u0639\u0627\u0644 \u06a9\u0631\u062f\u0646", callback_data=f"agbot:subs:enable:{service_id}")])
    rows.append([IButton("\U0001f504 \u0644\u06cc\u0646\u06a9 \u062c\u062f\u06cc\u062f", callback_data=f"agbot:subs:newlink:{service_id}")])
    rows.append([IButton("\U0001f5d1 \u062d\u0630\u0641", callback_data=f"agbot:subs:delete:{service_id}")])
    rows.append([IButton(BTN_BACK, callback_data="agbot:subs:back")])
    return _ikb(rows)


def subs_configs_keyboard(service_id: int, *, show_direct_config: bool = True,
                          show_sub_link: bool = True, show_auto_sub_link: bool = False,
                          show_sub_link_b64: bool = False, show_multi_server: bool = False,
                          show_multi_server_b64: bool = False) -> InlineKeyboardMarkup:
    """زیرمنوی «دریافت کانفیگ»: فقط دکمه‌های لینکی که ادمین فعال کرده (مثل ربات مشتری)."""
    rows = []
    if show_direct_config:
        rows.append([IButton("⚔️ کانفیگ مستقیم", callback_data=f"agbot:subs:cfgmenu:{service_id}:direct")])
    if show_sub_link:
        rows.append([IButton("🔗 لینک اشتراک", callback_data=f"agbot:subs:cfgmenu:{service_id}:sub_link")])
    if show_auto_sub_link:
        rows.append([IButton("🤖 اشتراک خودکار", callback_data=f"agbot:subs:cfgmenu:{service_id}:auto_sub")])
    if show_sub_link_b64:
        rows.append([IButton("🔐 لینک اشتراک b64", callback_data=f"agbot:subs:cfgmenu:{service_id}:sub_b64")])
    if show_multi_server:
        rows.append([IButton("🌐 لینک اشتراک هوشمند", callback_data=f"agbot:subs:cfgmenu:{service_id}:multi")])
    if show_multi_server_b64:
        rows.append([IButton("🌐 لینک اشتراک هوشمند b64", callback_data=f"agbot:subs:cfgmenu:{service_id}:multi_b64")])
    rows.append([IButton(BTN_BACK, callback_data=f"agbot:subs:detail:{service_id}")])
    return _ikb(rows)


# Wallet keyboards
def wallet_menu_keyboard():
    return _ikb([
        [IButton("\U0001f4b3 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a", callback_data="agbot:wallet:charge")],
        [IButton(BTN_BACK, callback_data="agbot:menu")],
    ])


# Plans keyboards
def plans_menu_keyboard(current_mode: str = "dynamic"):
    mode = str(current_mode or "dynamic").strip().lower()
    if mode == "fixed":
        settings_button = IButton("\u2699\ufe0f \u062a\u0646\u0638\u06cc\u0645 \u062b\u0627\u0628\u062a", callback_data="agbot:plans:fixed")
    else:
        settings_button = IButton("\u2699\ufe0f \u062a\u0646\u0638\u06cc\u0645 \u067e\u0648\u06cc\u0627", callback_data="agbot:plans:dynset")
    return _ikb([
        [IButton("\U0001f4cb \u0646\u0648\u0639 \u0646\u0645\u0627\u06cc\u0634 \u067e\u0644\u0646\u200c\u0647\u0627", callback_data="agbot:plans:mode")],
        [settings_button],
        [IButton(BTN_BACK, callback_data="agbot:menu")],
    ])


def plans_mode_keyboard(current_mode: str):
    dyn = "\u2705 \u067e\u0648\u06cc\u0627" if current_mode == "dynamic" else "\u274c \u067e\u0648\u06cc\u0627"
    fix = "\u2705 \u062b\u0627\u0628\u062a" if current_mode == "fixed" else "\u274c \u062b\u0627\u0628\u062a"
    return _ikb([
        [IButton(dyn, callback_data="agbot:plans:mode:toggle:dynamic")],
        [IButton(fix, callback_data="agbot:plans:mode:toggle:fixed")],
        [IButton(BTN_BACK, callback_data="agbot:plans:back")],
    ])


# Customer bot keyboards
def cbot_menu_keyboard(bot_active: bool):
    toggle = "\u2705 \u0641\u0639\u0627\u0644 \u06a9\u0631\u062f\u0646" if not bot_active else "\u274c \u063a\u06cc\u0631\u0641\u0639\u0627\u0644 \u06a9\u0631\u062f\u0646"
    rows = [
        [IButton(toggle, callback_data="agbot:cbot:activate")],
        [IButton("\U0001f511 \u062b\u0628\u062a \u062a\u0648\u06a9\u0646 \u0631\u0628\u0627\u062a", callback_data="agbot:cbot:token")],
        [IButton("\U0001f504 \u0631\u06cc\u0633\u062a\u0627\u0631\u062a \u0631\u0628\u0627\u062a", callback_data="agbot:cbot:restart")],
        [IButton(BTN_BACK, callback_data="agbot:menu")],
    ]
    return _ikb(rows)


# Ticket keyboards
def tickets_menu_keyboard():
    return _ikb([
        [IButton("\u23f3 \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627\u06cc \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631", callback_data="agbot:ticket:pending")],
        [IButton("\U0001f4ec \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627\u06cc \u0628\u0627\u0632", callback_data="agbot:ticket:open")],
        [IButton("\u2705 \u062a\u06cc\u06a9\u062a\u200c\u0647\u0627\u06cc \u0628\u0633\u062a\u0647", callback_data="agbot:ticket:closed")],
        [IButton(BTN_BACK, callback_data="agbot:menu")],
    ])


def ticket_detail_keyboard(ticket_id: int, status: str):
    rows = []
    if status in ("open", "pending"):
        rows.append([IButton("\U0001f4ac \u067e\u0627\u0633\u062e", callback_data=f"agbot:ticket:reply:{ticket_id}")])
        rows.append([IButton("\u2705 \u0628\u0633\u062a\u0646 \u062a\u06cc\u06a9\u062a", callback_data=f"agbot:ticket:close:{ticket_id}")])
    rows.append([IButton(BTN_BACK, callback_data="agbot:ticket:back")])
    return _ikb(rows)


# Settings root keyboard
def settings_menu_keyboard():
    return _ikb([
        [IButton("👥 مدیریت کاربران ربات", callback_data="agbot:set:users")],
        [
            IButton("📦 مدیریت سفارشات", callback_data="agbot:set:orders"),
            IButton("💳 مدیریت تراکنشات", callback_data="agbot:set:tx"),
        ],
        [
            IButton("🎁 مدیریت هدایا", callback_data="agbot:set:gifts"),
            IButton("📧 ارسال پیام همگانی", callback_data="agbot:set:broadcast"),
        ],
        [IButton("⚙️ تنظیمات", callback_data="agbot:set:config")],
    ])


def broadcast_menu_keyboard():
    return _ikb([
        [IButton("تمام کاربران", callback_data="agbot:broadcast:segment:all")],
        [IButton("تمام کاربران منقضی شده", callback_data="agbot:broadcast:segment:expired_all")],
        [IButton("کاربران بدون سفارش", callback_data="agbot:broadcast:segment:no_order")],
        [IButton("کاربران منقضی شده بیش از یک هفته", callback_data="agbot:broadcast:segment:expired_1w")],
        [IButton("کاربران منقضی شده بیش از دو هفته", callback_data="agbot:broadcast:segment:expired_2w")],
        [IButton("کاربران منقضی شده بیش از چهار هفته", callback_data="agbot:broadcast:segment:expired_4w")],
        [IButton("کاربران منقضی شده بیش از هشت هفته", callback_data="agbot:broadcast:segment:expired_8w")],
        [IButton(BTN_BACK, callback_data="agbot:set:back")],
    ])


def users_profile_keyboard(customer_id: int, telegram_id: int, back_callback: str = "agbot:set:users"):
    """کیبورد پروفایل کاربر برای نماینده"""
    rows = [
        [IButton("📋 لیست سرویس‌ها", callback_data=f"agbot:set:users:services:{customer_id}")],
        [IButton("📗 لیست سفارشات", callback_data=f"agbot:set:users:orders:{customer_id}")],
        [IButton("💵 لیست تراکنش‌ها", callback_data=f"agbot:set:users:tx:{customer_id}")],
        [IButton("🚫 مسدود/آزادسازی کاربر", callback_data=f"agbot:set:users:ban:{customer_id}")],
        [IButton("📑 لیست تیکت‌ها", callback_data=f"agbot:set:users:tickets:{customer_id}")],
        [IButton("📨 ارسال پیام", callback_data=f"agbot:set:users:message:{telegram_id}")],
        [IButton(BTN_BACK, callback_data=back_callback)],
    ]
    return _ikb(rows)


def settings_sub_menu_keyboard(prefix: str, list_label: Optional[str] = None):
    if list_label is None:
        list_label = "\U0001f4cb \u0644\u06cc\u0633\u062a"
    return _ikb([
        [IButton(list_label, callback_data=f"agbot:{prefix}:list")],
        [IButton("\U0001f50d \u062c\u0633\u062a\u062c\u0648", callback_data=f"agbot:{prefix}:search")],
        [IButton(BTN_BACK, callback_data="agbot:set:back")],
    ])


def tx_menu_keyboard():
    return _ikb([
        [IButton("\u2705 \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f\u0647", callback_data="agbot:set:tx:approved")],
        [IButton("\u274c \u0631\u062f \u0634\u062f\u0647", callback_data="agbot:set:tx:rejected")],
        [IButton("\u23f3 \u062f\u0631 \u0627\u0646\u062a\u0638\u0627\u0631", callback_data="agbot:set:tx:pending")],
        [IButton("\U0001f4b3 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a", callback_data="agbot:set:tx:card")],
        [IButton("\U0001f50d \u062c\u0633\u062a\u062c\u0648", callback_data="agbot:set:tx:search")],
        [IButton(BTN_BACK, callback_data="agbot:set:back")],
    ])


def config_menu_keyboard():
    return _ikb([
        [IButton("🛒 تنظیمات خرید و تمدید", callback_data="agbot:set:cfg:shop")],
        [IButton("💳 تنظیمات پرداخت", callback_data="agbot:set:cfg:payment")],
        [IButton("🔒 تنظیمات عضویت اجباری", callback_data="agbot:set:cfg:forcejoin")],
        [IButton(BTN_BACK, callback_data="agbot:set:back")],
    ])


def shop_settings_keyboard(buy_enabled: bool, renew_enabled: bool):
    buy = "\u2705 \u0627\u0645\u06a9\u0627\u0646 \u062e\u0631\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9" if buy_enabled else "\u274c \u0627\u0645\u06a9\u0627\u0646 \u062e\u0631\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9"
    renew = "\u2705 \u0627\u0645\u06a9\u0627\u0646 \u062a\u0645\u062f\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9" if renew_enabled else "\u274c \u0627\u0645\u06a9\u0627\u0646 \u062a\u0645\u062f\u06cc\u062f \u0627\u0634\u062a\u0631\u0627\u06a9"
    return _ikb([
        [IButton(buy, callback_data="agbot:shop:buy")],
        [IButton(renew, callback_data="agbot:shop:renew")],
        [IButton(BTN_BACK, callback_data="agbot:set:cfg:back")],
    ])


def payment_settings_keyboard():
    return _ikb([
        [IButton("\U0001f4b3 \u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a", callback_data="agbot:pay:menu")],
        [IButton(BTN_BACK, callback_data="agbot:set:cfg:back")],
    ])


def card_settings_keyboard(card_enabled: bool, last4: bool, rand_tx: bool, sms_auto: bool):
    return _ikb([
        [IButton("\u2705 \u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a" if card_enabled else "\u274c \u067e\u0631\u062f\u0627\u062e\u062a \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a", callback_data="agbot:pay:card")],
        [IButton("\u2705 \u0627\u0644\u0632\u0627\u0645 4 \u0631\u0642\u0645 \u0622\u062e\u0631 \u06a9\u0627\u0631\u062a" if last4 else "\u274c \u0627\u0644\u0632\u0627\u0645 4 \u0631\u0642\u0645 \u0622\u062e\u0631 \u06a9\u0627\u0631\u062a", callback_data="agbot:pay:last4")],
        [IButton("\u2705 \u0645\u0634\u062e\u0635\u0647 \u062a\u0635\u0627\u062f\u0641\u06cc \u062a\u0631\u0627\u06a9\u0646\u0634" if rand_tx else "\u274c \u0645\u0634\u062e\u0635\u0647 \u062a\u0635\u0627\u062f\u0641\u06cc \u062a\u0631\u0627\u06a9\u0646\u0634", callback_data="agbot:pay:randtx")],
        [IButton("🤖 تایید خودکار SMS بانک", callback_data="agbot:pay:smsauto")],
        [IButton("💳 لیست کارت‌ها", callback_data="agbot:pay:cards")],
        [IButton("\u270f\ufe0f \u062a\u0646\u0638\u06cc\u0645 \u0645\u062a\u0646 \u06a9\u0627\u0631\u062a \u0628\u0647 \u06a9\u0627\u0631\u062a", callback_data="agbot:pay:cardtext")],
        [IButton(BTN_BACK, callback_data="agbot:set:cfg:back")],
    ])


def payment_cards_list_keyboard(cards):
    rows = []
    for c in cards:
        number = str(c.get("card_number") or "").strip()
        if not number:
            continue
        bank = str(c.get("bank_name") or "").strip()
        label = f"{bank} - {number}" if bank else number
        rows.append([IButton(label, callback_data=f"agbot:pay:cardedit:{c['id']}")])
    rows.append([IButton("➕ افزودن کارت", callback_data="agbot:pay:cardadd")])
    rows.append([IButton(BTN_BACK, callback_data="agbot:pay:menu")])
    return _ikb(rows)


def sms_webhook_settings_keyboard(enabled: bool):
    enabled_icon = "✅" if enabled else "❌"
    return _ikb([
        [IButton(f"تایید خودکار SMS | {enabled_icon}", callback_data="agbot:pay:smsauto:toggle")],
        [IButton("🔑 ساخت / تعویض Secret", callback_data="agbot:pay:smsauto:regen")],
        [IButton("👁 نمایش Secret برای اپ", callback_data="agbot:pay:smsauto:show")],
        [IButton("📱 راهنمای اتصال اپ", callback_data="agbot:pay:smsauto:help")],
        [IButton(BTN_BACK, callback_data="agbot:pay:menu")],
    ])


def pagination_keyboard(base_callback: str, page: int, total_pages: int, back_callback: str):
    rows = []
    nav = []
    if page > 1:
        nav.append(IButton("\u2b05\ufe0f \u0642\u0628\u0644\u06cc", callback_data=f"{base_callback}:{page - 1}"))
    if page < total_pages:
        nav.append(IButton("\u0628\u0639\u062f\u06cc \u27a1\ufe0f", callback_data=f"{base_callback}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([IButton(BTN_BACK, callback_data=back_callback)])
    return _ikb(rows)


# Fixed plan keyboards
def plans_cats_keyboard(cats):
    rows = []
    for c in cats:
        count = int(c.get('plan_count', 0) or 0)
        rows.append([IButton(f"\U0001f6d2 {c['title']}  \u2022  {count} \u067e\u0644\u0646", callback_data=f"agbot:plans:fixed:cat:{c['id']}")])
    rows.append([IButton('\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u062f\u0633\u062a\u0647 \u062c\u062f\u06cc\u062f', callback_data='agbot:plans:fixed:cat_add')])
    rows.append([IButton(BTN_BACK, callback_data='agbot:plans:back')])
    return _ikb(rows)


def plans_cat_detail_keyboard(cat_id: int):
    return _ikb([
        [IButton('\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u067e\u0644\u0646 \u062c\u062f\u06cc\u062f', callback_data=f'agbot:plans:fixed:plan_add:{cat_id}')],
        [IButton('\U0001f4cb \u0645\u0634\u0627\u0647\u062f\u0647 \u0648 \u0645\u062f\u06cc\u0631\u06cc\u062a \u067e\u0644\u0646\u200c\u0647\u0627', callback_data=f'agbot:plans:fixed:plans:{cat_id}')],
        [IButton('\u270f\ufe0f \u0648\u06cc\u0631\u0627\u06cc\u0634 \u0639\u0646\u0648\u0627\u0646 \u062f\u0633\u062a\u0647', callback_data=f'agbot:plans:fixed:cat_edit:{cat_id}')],
        [IButton('\U0001f5d1 \u062d\u0630\u0641 \u062f\u0633\u062a\u0647', callback_data=f'agbot:plans:fixed:cat_del_ask:{cat_id}')],
        [IButton(BTN_BACK, callback_data='agbot:plans:fixed')],
    ])


def plans_cat_del_confirm_keyboard(cat_id: int):
    return _ikb([
        [IButton('\u26a0\ufe0f \u062a\u0627\u06cc\u06cc\u062f \u062d\u0630\u0641 \u062f\u0633\u062a\u0647', callback_data=f'agbot:plans:fixed:cat_del:{cat_id}')],
        [IButton('\u274c \u0627\u0646\u0635\u0631\u0627\u0641', callback_data=f'agbot:plans:fixed:cat:{cat_id}')],
    ])


def plans_cat_del_keyboard(cats):
    rows = []
    for c in cats:
        rows.append([IButton(f"\U0001f5d1 {c['title']}", callback_data=f"agbot:plans:fixed:cat_del:{c['id']}")])
    rows.append([IButton(BTN_BACK, callback_data='agbot:plans:fixed')])
    return _ikb(rows)


def plans_plans_keyboard(plans, cat_id):
    rows = []
    for p in plans:
        try:
            gb_val = float(p.get('gb') or 0)
        except (TypeError, ValueError):
            gb_val = 0.0
        vol_txt = '\u0646\u0627\u0645\u062d\u062f\u0648\u062f' if gb_val == 0 else f'{gb_val:g} \u06af\u06cc\u06af'
        days_val = int(p.get('days') or 0)
        days_txt = '\u0646\u0627\u0645\u062d\u062f\u0648\u062f' if days_val == 0 else f'{days_val} \u0631\u0648\u0632'
        label = f"\U0001f4e6 {p['title']} | {vol_txt} | {days_txt} | {int(p['price']):,} \u062a"
        rows.append([IButton(label, callback_data=f"agbot:plans:fixed:plan:{p['id']}")])
    rows.append([IButton('\u2795 \u0627\u0641\u0632\u0648\u062f\u0646 \u067e\u0644\u0646', callback_data=f"agbot:plans:fixed:plan_add:{cat_id}")])
    rows.append([IButton('\U0001f5d1 \u062d\u0630\u0641 \u067e\u0644\u0646', callback_data=f"agbot:plans:fixed:plan_del_menu:{cat_id}")])
    rows.append([IButton(BTN_BACK, callback_data=f"agbot:plans:fixed:cat:{cat_id}")])
    return _ikb(rows)


def plans_plan_del_keyboard(plans, cid):
    rows = []
    for p in plans:
        rows.append([IButton(f"\U0001f5d1 {p['title']}", callback_data=f"agbot:plans:fixed:plan_del:{p['id']}")])
    rows.append([IButton(BTN_BACK, callback_data=f"agbot:plans:fixed:plans:{cid}")])
    return _ikb(rows)


# Dynamic plan settings keyboard
def dyn_settings_keyboard():
    return _ikb([
        [IButton('\U0001f4b0 \u0642\u06cc\u0645\u062a \u0647\u0631 \u06af\u06cc\u06af', callback_data='agbot:plans:dyn_edit:price_per_gb')],
        [IButton('\U0001f4b0 \u0642\u06cc\u0645\u062a \u0647\u0631 \u0645\u0627\u0647', callback_data='agbot:plans:dyn_edit:price_per_month')],
        [IButton('\U0001f4ca \u0645\u062d\u062f\u0648\u062f\u0647 \u062d\u062c\u0645', callback_data='agbot:plans:dyn_edit:volume_range')],
        [IButton('\u23f0 \u0645\u062d\u062f\u0648\u062f\u0647 \u0632\u0645\u0627\u0646', callback_data='agbot:plans:dyn_edit:time_range')],
        [IButton(BTN_BACK, callback_data='agbot:plans:back')],
    ])


def agent_dynamic_wizard_keyboard(server_id: int, gb: int, months: int, price: int, off_percent: int = 0, wholesale: int = 0):
    price_str = f"{price:,}"
    wholesale_str = f"{wholesale:,}"
    rows = [
        [IButton("\U0001f4ca \u062d\u062c\u0645", callback_data="noop")],
        [
            IButton("\u2796 10", callback_data=f"agbot:subs:wiz:gb_dec10:{server_id}"),
            IButton("\u2796", callback_data=f"agbot:subs:wiz:gb_dec:{server_id}"),
            IButton(f"{gb} \u06af\u06cc\u06af\u0627\u0628\u0627\u06cc\u062a", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:wiz:gb_inc:{server_id}"),
            IButton("\u2795 10", callback_data=f"agbot:subs:wiz:gb_inc10:{server_id}"),
        ],
        [IButton("\u23f3 \u0632\u0645\u0627\u0646", callback_data="noop")],
        [
            IButton("\u2796", callback_data=f"agbot:subs:wiz:month_dec:{server_id}"),
            IButton(f"{months} \u0645\u0627\u0647\u0647", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:wiz:month_inc:{server_id}"),
        ],
        [
            IButton(f"\U0001f4b0 \u0642\u06cc\u0645\u062a \u0639\u0645\u062f\u0647: {wholesale_str} \u062a\u0648\u0645\u0627\u0646", callback_data="noop"),
        ],
        [
            IButton(f"\U0001f4b8 \u0642\u06cc\u0645\u062a \u0641\u0631\u0648\u0634: {price_str} \u062a\u0648\u0645\u0627\u0646", callback_data="noop"),
            IButton(f"\U0001f3f7 \u062a\u062e\u0641\u06cc\u0641: {off_percent}%", callback_data="noop"),
        ],
        [IButton("\U0001f4b3 \u062a\u0627\u06cc\u06cc\u062f \u0648 \u0633\u0627\u062e\u062a\u0646", callback_data=f"agbot:subs:wiz:confirm:{server_id}")],
        [IButton(BTN_BACK, callback_data="agbot:subs:picksrv_back")],
    ]
    return _ikb(rows)


def agent_renew_wizard_keyboard(service_id: int, gb: int, months: int, price: int, off_percent: int = 0, wholesale: int = 0):
    """ویزارد تمدید اشتراک — دقیقاً مثل پنل ساخت اشتراک ولی برای تمدید."""
    price_str = f"{price:,}"
    wholesale_str = f"{wholesale:,}"
    rows = [
        [IButton("\U0001f4ca \u062d\u062c\u0645 \u062a\u0645\u062f\u06cc\u062f", callback_data="noop")],
        [
            IButton("\u2796 10", callback_data=f"agbot:subs:rewiz:gb_dec10:{service_id}"),
            IButton("\u2796", callback_data=f"agbot:subs:rewiz:gb_dec:{service_id}"),
            IButton(f"{gb} \u06af\u06cc\u06af\u0627\u0628\u0627\u06cc\u062a", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:rewiz:gb_inc:{service_id}"),
            IButton("\u2795 10", callback_data=f"agbot:subs:rewiz:gb_inc10:{service_id}"),
        ],
        [IButton("\u23f3 \u0645\u062f\u062a \u062a\u0645\u062f\u06cc\u062f", callback_data="noop")],
        [
            IButton("\u2796", callback_data=f"agbot:subs:rewiz:month_dec:{service_id}"),
            IButton(f"{months} \u0645\u0627\u0647", callback_data="noop"),
            IButton("\u2795", callback_data=f"agbot:subs:rewiz:month_inc:{service_id}"),
        ],
        [
            IButton(f"\U0001f4b0 \u06a9\u0633\u0631 \u0627\u0632 \u06a9\u06cc\u0641 \u067e\u0648\u0644: {wholesale_str} \u062a\u0648\u0645\u0627\u0646", callback_data="noop"),
        ],
        [
            IButton(f"\U0001f4b8 \u0642\u06cc\u0645\u062a \u0641\u0631\u0648\u0634: {price_str} \u062a\u0648\u0645\u0627\u0646", callback_data="noop"),
            IButton(f"\U0001f3f7 \u062a\u062e\u0641\u06cc\u0641: {off_percent}%", callback_data="noop"),
        ],
        [IButton("\u2705 \u062a\u0627\u06cc\u06cc\u062f \u0648 \u062a\u0645\u062f\u06cc\u062f", callback_data=f"agbot:subs:rewiz:confirm:{service_id}")],
        [IButton(BTN_BACK, callback_data=f"agbot:subs:detail:{service_id}")],
    ]
    return _ikb(rows)
