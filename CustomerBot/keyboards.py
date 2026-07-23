from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KeyboardButton

from CustomerBot.constants import (
    BTN_BACK,
    BTN_BUY,
    BTN_FAQ,
    BTN_GUIDE,
    BTN_RENEW,
    BTN_STATUS,
    BTN_SUPPORT,
    BTN_TRIAL,
)


def main_menu_keyboard(show_renew: bool = True):
    keyboard = [[KeyboardButton(BTN_STATUS)]]
    if show_renew:
        keyboard.append([KeyboardButton(BTN_BUY), KeyboardButton(BTN_RENEW)])
    else:
        keyboard.append([KeyboardButton(BTN_BUY)])
    keyboard.extend([
        [KeyboardButton(BTN_TRIAL)],
        [KeyboardButton(BTN_GUIDE), KeyboardButton(BTN_SUPPORT)],
        [KeyboardButton(BTN_FAQ)],
    ])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_BACK)]], resize_keyboard=True)


def receipt_cancel_keyboard():
    """دکمه لغو برای صفحه ارسال رسید"""
    return ReplyKeyboardMarkup([[KeyboardButton("🚫 لغو")]], resize_keyboard=True)


def location_keyboard(servers, columns: int = 1):
    rows = []
    cols = int(columns) if str(columns).isdigit() else 1
    if cols not in {1, 2, 3}:
        cols = 1
    btns = []
    for s in servers:
        title = (s.get('title') or "").strip()
        flag = "🏳️"
        if "ترکیه" in title: flag = "🇹🇷"
        elif "هلند" in title: flag = "🇳🇱"
        elif "آلمان" in title: flag = "🇩🇪"
        elif "فرانسه" in title: flag = "🇫🇷"
        elif "امریک" in title: flag = "🇺🇸"
        has_location_word = "لوکیشن" in title
        has_flag = flag != "🏳️" and flag in title
        if has_location_word:
            if has_flag:
                btn_text = title
            else:
                btn_text = f"{title} {flag}" if flag != "🏳️" else title
        else:
            btn_text = f"لوکیشن {flag} {title}" if flag != "🏳️" else f"لوکیشن {title}"
        btns.append(InlineKeyboardButton(btn_text, callback_data=f"buy:loc:{s['id']}"))
    for i in range(0, len(btns), cols):
        chunk = btns[i:i + cols]
        rows.append(list(reversed(chunk)))
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="buy:exit_main")])
    return InlineKeyboardMarkup(rows)


def trial_location_keyboard(servers):
    rows = []
    for s in servers:
        title = (s.get('title') or "").strip()
        flag = "🏳️"
        if "ترکیه" in title: flag = "🇹🇷"
        elif "هلند" in title: flag = "🇳🇱"
        elif "آلمان" in title: flag = "🇩🇪"
        elif "فرانسه" in title: flag = "🇫🇷"
        elif "امریک" in title: flag = "🇺🇸"
        has_location_word = "لوکیشن" in title
        has_flag = flag != "🏳️" and flag in title
        if has_location_word:
            if has_flag:
                btn_text = title
            else:
                btn_text = f"{title} {flag}" if flag != "🏳️" else title
        else:
            btn_text = f"لوکیشن {flag} {title}" if flag != "🏳️" else f"لوکیشن {title}"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"trial:loc:{s['id']}")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="trial:back")])
    return InlineKeyboardMarkup(rows)


def category_keyboard(categories, server_id):
    rows = []
    for cat in sorted(categories, key=lambda x: x.get('priority', 0)):
        rows.append([InlineKeyboardButton(cat['title'], callback_data=f"buy:cat:{server_id}:{cat['id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به لوکیشن‌ها", callback_data="buy:back_main")])
    return InlineKeyboardMarkup(rows)


def plans_keyboard(plans, server_id, cat_id, columns: int = 1, *,
                   unlimited_volume: bool = False, unlimited_volume_from: int = 1000,
                   unlimited_time: bool = False, unlimited_time_from: int = 365,
                   sort_by_priority: bool = True, back_to_categories: bool = True):
    rows = []
    cols = int(columns) if str(columns).isdigit() else 1
    if cols not in {1, 2}:
        cols = 1
    btns = []
    ordered_plans = sorted(plans, key=lambda x: x.get('priority', 0)) if sort_by_priority else list(plans)
    for p in ordered_plans:
        price_str = f"{p['price']:,} تومان"
        try:
            gb_val = float(p.get("gb") or 0)
        except (TypeError, ValueError):
            gb_val = 0.0
        try:
            days_val = int(p.get("days") or 0)
        except (TypeError, ValueError):
            days_val = 0
        vol_txt = "نامحدود" if (unlimited_volume and gb_val >= int(unlimited_volume_from)) else f"{gb_val:g} گیگ"
        day_txt = "نامحدود" if (unlimited_time and days_val >= int(unlimited_time_from)) else f"{days_val} روز"
        btn_text = f"{p['title']} | {vol_txt} | {day_txt} - {price_str}"
        btns.append(InlineKeyboardButton(btn_text, callback_data=f"buy:plan:{server_id}:{p['id']}"))
    for i in range(0, len(btns), cols):
        chunk = btns[i:i + cols]
        rows.append(list(reversed(chunk)))
    if back_to_categories:
        rows.append([InlineKeyboardButton("🔙 بازگشت به دسته‌بندی‌ها", callback_data=f"buy:loc:{server_id}")])
    else:
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="buy:back_main")])
    return InlineKeyboardMarkup(rows)


def confirm_buy_keyboard(server_id, plan_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و پرداخت", callback_data=f"buy:confirm:{server_id}:{plan_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="buy:back_main")]
    ])


def selected_plan_keyboard(server_id: int, gb: int, days: int, price: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 پرداخت مستقیم", callback_data=f"buy:pay_direct:{server_id}:{gb}:{days}:{price}")],
        [InlineKeyboardButton("بازگشت", callback_data=f"buy:loc:{server_id}")],
    ])


def buy_wizard_keyboard(server_id, gb, months, price, off_percent=0):
    price_str = f"{price:,}"
    keyboard = [
        [InlineKeyboardButton("📊 حجم", callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:gb_dec"),
            InlineKeyboardButton(f"{gb} گیگابایت", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:gb_inc")
        ],
        [InlineKeyboardButton("⏳ زمان", callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:month_dec"),
            InlineKeyboardButton(f"{months} ماهه", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:month_inc")
        ],
        [
            InlineKeyboardButton(f"🏷 تخفیف: {off_percent}%", callback_data="noop"),
            InlineKeyboardButton(f"💰 قیمت: {price_str} تومان", callback_data="noop")
        ],
        [InlineKeyboardButton("💳 تایید و خرید", callback_data=f"buy:confirm_dyn:{server_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="buy:back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def mixed_buy_keyboard(server_id, gb, days, price, plans=None, off_percent=0):
    price_str = f"{price:,}"
    keyboard = [
        [InlineKeyboardButton("🎛 بسته دلخواه خود را بسازید", callback_data="noop")],
        [InlineKeyboardButton("📊 حجم", callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:gb_dec"),
            InlineKeyboardButton(f"{gb} گیگابایت", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:gb_inc")
        ],
        [InlineKeyboardButton("⏳ زمان", callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:month_dec"),
            InlineKeyboardButton(f"{days} ماهه", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:month_inc")
        ],
        [
            InlineKeyboardButton(f"🏷 تخفیف: {off_percent}%", callback_data="noop"),
            InlineKeyboardButton(f"💰 قیمت: {price_str}", callback_data="noop")
        ],
        [InlineKeyboardButton("💳 خرید بسته دلخواه", callback_data=f"buy:confirm_dyn:{server_id}")],
        [InlineKeyboardButton("📋 پلن های آماده", callback_data=f"wiz:{server_id}:show_fixed")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="buy:back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def mixed_mode_keyboard(server_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 بسته‌های ثابت (پیشنهادی)", callback_data=f"buy:mixed:fixed:{server_id}")],
        [InlineKeyboardButton("🎛 بسته دلخواه (تعیین حجم و زمان)", callback_data=f"buy:mixed:dyn:{server_id}")],
        [InlineKeyboardButton("🔙 بازگشت به لوکیشن‌ها", callback_data="buy:back_main")]
    ])




def confirm_payment_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✅ پرداخت کردم، ارسال رسید")],
        [KeyboardButton("بازگشت")],
    ], resize_keyboard=True)


def confirm_payment_inline_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ پرداخت کردم | ارسال رسید", callback_data="pay:receipt_done")],
        [InlineKeyboardButton("🔙 انصراف و بازگشت", callback_data="pay:cancel")],
    ])


def cancel_inline_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 لغو", callback_data="pay:cancel")],
    ])


def subscription_status_keyboard(service_id=None, *, show_direct_config: bool = True,
                                  show_sub_link: bool = True, show_configs: bool = False,
                                  show_detach: bool = False):
    keyboard = [
        [InlineKeyboardButton("کانفیگ ها📝", callback_data=f"status:configs:{service_id}")],
        [InlineKeyboardButton("تمدید اشتراک♾", callback_data=f"status:renew:{service_id}")],
        [InlineKeyboardButton("تغییر نام اشتراک✏️", callback_data=f"status:rename:{service_id}")],
        [InlineKeyboardButton("تغییر لینک اشتراک🚨", callback_data=f"status:replace_link:{service_id}", style='danger')],
    ]
    if show_detach:
        keyboard.append([InlineKeyboardButton("❌ جداسازی اشتراک⭕", callback_data=f"status:detach:{service_id}", style='danger')])
    return InlineKeyboardMarkup(keyboard)


def replace_subscription_link_confirm_keyboard(service_id=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ تایید تغییر لینک🚨", callback_data=f"status:replace_link:{service_id}:confirm", style='danger')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"status:menu:{service_id}")],
    ])


def direct_configs_keyboard(service_id=None, *, show_vless: bool = True,
                             show_vmess: bool = True, show_trojan: bool = True):
    rows = []
    if show_vless:
        rows.append([InlineKeyboardButton("Vless", callback_data=f"status:directcfg:{service_id}:vless")])
    if show_vmess:
        rows.append([InlineKeyboardButton("Vmess", callback_data=f"status:directcfg:{service_id}:vmess")])
    if show_trojan:
        rows.append([InlineKeyboardButton("Trojan", callback_data=f"status:directcfg:{service_id}:trojan")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data=f"status:menu:{service_id}")])
    return InlineKeyboardMarkup(rows)


def subscription_configs_keyboard(service_id=None, *, show_direct_config: bool = True,
                                    show_sub_link: bool = True, show_auto_sub_link: bool = False,
                                    show_sub_link_b64: bool = False, show_multi_server: bool = False,
                                    show_multi_server_b64: bool = False):
    rows = []
    if show_direct_config:
        rows.append([InlineKeyboardButton("کانفیگ مستقیم", callback_data=f"status:direct:{service_id}")])
    if show_sub_link:
        rows.append([InlineKeyboardButton("لینک اشتراک", callback_data=f"status:sub_link:{service_id}")])
    if show_auto_sub_link:
        rows.append([InlineKeyboardButton("اشتراک خودکار", callback_data=f"status:auto_sub:{service_id}")])
    if show_sub_link_b64:
        rows.append([InlineKeyboardButton("لینک اشتراک b64", callback_data=f"status:sub_b64:{service_id}")])
    if show_multi_server:
        rows.append([InlineKeyboardButton("🌐 لینک اشتراک هوشمند", callback_data=f"status:multi:{service_id}")])
    if show_multi_server_b64:
        rows.append([InlineKeyboardButton("🌐 لینک اشتراک هوشمند b64", callback_data=f"status:multi_b64:{service_id}")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data=f"status:menu:{service_id}")])
    return InlineKeyboardMarkup(rows)


def subscription_links_keyboard(service_id=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 راهنمای اتصال", callback_data=f"status:guide:{service_id}")],
        [InlineKeyboardButton("🔙بازگشت", callback_data=f"status:menu:{service_id}")],
    ])


def services_list_keyboard(services):
    rows = []
    for s in services:
        name = (s.get("name") or "").strip() or f"اشتراک {s.get('id')}"
        sid = int(s.get("id") or 0)
        if sid <= 0:
            continue
        rows.append([InlineKeyboardButton(name, callback_data=f"status:list:{sid}")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="status:list_back:0")])
    return InlineKeyboardMarkup(rows)


def renew_services_keyboard(services):
    rows = []
    for s in services:
        name = (s.get("name") or "").strip() or f"اشتراک {s.get('id')}"
        sid = int(s.get("id") or 0)
        if sid <= 0:
            continue
        rows.append([InlineKeyboardButton(name, callback_data=f"renew:svc:{sid}")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="renew:back:0")])
    return InlineKeyboardMarkup(rows)


def support_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❗️سوالات متداول", callback_data="support:faq")],
        [InlineKeyboardButton("📬تیکت‌های من", callback_data="support:my:1")],
        [InlineKeyboardButton("📩ایجاد تیکت", callback_data="support:new")],
        [InlineKeyboardButton("🔙بازگشت", callback_data="support:back_main")],
    ])


def ticket_skip_screenshot_keyboard(mode: str = "new"):
    flow = str(mode or "new").strip().lower()
    if flow not in {"new", "reply"}:
        flow = "new"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️رد کردن", callback_data=f"support:{flow}:skip")],
        [InlineKeyboardButton("❌لغو", callback_data=f"support:{flow}:cancel")],
    ])


def ticket_confirm_keyboard(mode: str = "new"):
    flow = str(mode or "new").strip().lower()
    if flow not in {"new", "reply"}:
        flow = "new"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ارسال", callback_data=f"support:{flow}:send"),
         InlineKeyboardButton("✏️ویرایش", callback_data=f"support:{flow}:edit")],
        [InlineKeyboardButton("❌لغو", callback_data=f"support:{flow}:cancel")],
    ])


def user_tickets_list_keyboard(tickets, page: int, total_pages: int):
    rows = []
    current = []
    for t in tickets:
        code = str(t.get("ticket_code") or "").strip()
        if not code:
            continue
        current.append(InlineKeyboardButton(code, callback_data=f"support:view:{code}:{page}"))
        if len(current) == 3:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"support:my:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{max(1, total_pages)}", callback_data="support:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"support:my:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="support:menu")])
    return InlineKeyboardMarkup(rows)


def user_ticket_detail_keyboard(ticket_code: int, can_reply: bool = True, is_closed: bool = False):
    rows = []
    if can_reply:
        rows.append([InlineKeyboardButton("📩پاسخ", callback_data=f"support:reply:{int(ticket_code)}")])
        rows.append([InlineKeyboardButton("🚫بستن تیکت", callback_data=f"support:close:{int(ticket_code)}")])
    rows.append([InlineKeyboardButton("🔙بازگشت", callback_data="support:menu")])
    return InlineKeyboardMarkup(rows)


def force_join_keyboard(join_url: str = ""):
    rows = []
    if str(join_url or "").strip():
        rows.append([InlineKeyboardButton("📢 عضویت در کانال", url=join_url)])
    rows.append([InlineKeyboardButton("✅ بررسی عضویت", callback_data="forcejoin:check")])
    return InlineKeyboardMarkup(rows)


def guide_os_keyboard(back_token: str = "m"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 راهنمای اندروید", callback_data=f"guide:android:{back_token}")],
        [InlineKeyboardButton("📱 راهنمای IOS", callback_data=f"guide:ios:{back_token}")],
        [InlineKeyboardButton("🖥️ راهنمای ویندوز", callback_data=f"guide:windows:{back_token}")],
        [InlineKeyboardButton("💻 راهنمای مک", callback_data=f"guide:mac:{back_token}")],
        [InlineKeyboardButton("🖥️ راهنمای لینوکس", callback_data=f"guide:linux:{back_token}")],
        [InlineKeyboardButton("🔙بازگشت", callback_data=f"guide:back:{back_token}")],
    ])

