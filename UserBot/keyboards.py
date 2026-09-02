# UserBot/keyboards.py

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup

from Shared import i18n
from Shared.tg_button_styles import inline_button as InlineKeyboardButton
from Shared.tg_button_styles import keyboard_button as KeyboardButton

# کلیدهای i18n دکمه‌های منوی اصلی (برای مچر چندزبانه)
MENU_BTN_KEYS = (
    "menu_status", "menu_renew", "menu_buy", "menu_connect", "menu_trial",
    "menu_wallet", "menu_support", "menu_guide", "menu_faq", "menu_invite",
    "btn_back", "btn_pay_done", "btn_cancel",
)


def _bl(key: str, lang: str = "fa") -> str:
    """لیبل دکمه از i18n."""
    return i18n.t(key, lang)

# --- منوی اصلی (Reply Keyboard) ---
def main_menu_keyboard(show_renew: bool = True, show_invite: bool = True, lang: str = "fa"):
    keyboard = [[KeyboardButton(_bl("menu_status", lang))]]
    if show_renew:
        keyboard.append([KeyboardButton(_bl("menu_renew", lang)), KeyboardButton(_bl("menu_buy", lang))])
    else:
        keyboard.append([KeyboardButton(_bl("menu_buy", lang))])
    keyboard.extend([
        [KeyboardButton(_bl("menu_connect", lang))],
        [KeyboardButton(_bl("menu_trial", lang)), KeyboardButton(_bl("menu_wallet", lang))],
        [KeyboardButton(_bl("menu_support", lang)), KeyboardButton(_bl("menu_guide", lang)), KeyboardButton(_bl("menu_faq", lang))],
    ])
    if show_invite:
        keyboard.append([KeyboardButton(_bl("menu_invite", lang))])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def language_keyboard():
    rows = []
    langs = i18n.supported_langs()
    for i in range(0, len(langs), 2):
        rows.append([InlineKeyboardButton(i18n.lang_display_name(lg), callback_data=f"lang:set:{lg}") for lg in langs[i:i + 2]])
    return InlineKeyboardMarkup(rows)

# --- دکمه بازگشت (برای مراحل پرداخت) ---
def cancel_keyboard(lang: str = "fa"):
    return ReplyKeyboardMarkup([[KeyboardButton(i18n.t("btn_back", lang))]], resize_keyboard=True)

# --- دکمه بازگشت قرمز رنگ برای صفحه ارسال رسید ---
def receipt_cancel_keyboard(lang: str = "fa"):
    return ReplyKeyboardMarkup([[KeyboardButton(i18n.t("btn_back", lang), style="danger")]], resize_keyboard=True)

# --- انتخاب لوکیشن (Inline) ---
def location_keyboard(servers, columns: int = 1, lang: str = "fa"):
    rows = []
    cols = int(columns) if str(columns).isdigit() else 1
    if cols not in {1, 2, 3}:
        cols = 1
    btns = []
    for s in servers:
        title = (s.get('title') or "").strip()
        # تشخیص پرچم از روی اسم (ساده)
        flag = "🏳️"
        if i18n.t("flag_turkey", lang) in title or "ترکیه" in title: flag = "🇹🇷"
        elif i18n.t("flag_netherlands", lang) in title or "هلند" in title: flag = "🇳🇱"
        elif i18n.t("flag_germany", lang) in title or "آلمان" in title: flag = "🇩🇪"
        elif i18n.t("flag_france", lang) in title or "فرانسه" in title: flag = "🇫🇷"
        elif i18n.t("flag_america", lang) in title or "امریک" in title: flag = "🇺🇸"

        # اگر عنوان خودش «لوکیشن» یا پرچم داشت، دوباره اضافه نکن
        has_location_word = _bl("word_location", lang) in title
        has_flag = flag != "🏳️" and flag in title
        if has_location_word:
            if has_flag:
                btn_text = title
            else:
                btn_text = f"{title} {flag}" if flag != "🏳️" else title
        else:
            btn_text = f"{i18n.t('word_location', lang)} {flag} {title}" if flag != "🏳️" else f"{i18n.t('word_location', lang)} {title}"

        btns.append(InlineKeyboardButton(btn_text, callback_data=f"buy:loc:{s.get('id', '')}"))

    for i in range(0, len(btns), cols):
        chunk = btns[i:i + cols]
        rows.append(list(reversed(chunk)))
    
    # بازگشت از خودِ لیست سرورها باید به منوی اصلی برگردد
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="buy:exit_main")])
    return InlineKeyboardMarkup(rows)


def trial_location_keyboard(servers, lang: str = "fa"):
    rows = []
    for s in servers:
        title = (s.get('title') or "").strip()
        flag = "🏳️"
        if "ترکیه" in title:
            flag = "🇹🇷"
        elif "هلند" in title:
            flag = "🇳🇱"
        elif "آلمان" in title:
            flag = "🇩🇪"
        elif "فرانسه" in title:
            flag = "🇫🇷"
        elif "امریک" in title:
            flag = "🇺🇸"

        has_location_word = _bl("word_location", lang) in title
        has_flag = flag != "🏳️" and flag in title
        if has_location_word:
            if has_flag:
                btn_text = title
            else:
                btn_text = f"{title} {flag}" if flag != "🏳️" else title
        else:
            btn_text = f"{i18n.t('word_location', lang)} {flag} {title}" if flag != "🏳️" else f"{i18n.t('word_location', lang)} {title}"

        rows.append([InlineKeyboardButton(btn_text, callback_data=f"trial:loc:{s['id']}")])

    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="trial:back")])
    return InlineKeyboardMarkup(rows)

# --- ویزارد خرید (انتخاب حجم و زمان) ---
def mixed_buy_keyboard(server_id, gb, days, price, plans=None, off_percent=0, lang: str = "fa"):
    """کیبورد ترکیبی برای نمایش همزمان ویزارد و پلن‌های آماده"""
    price_str = f"{price:,}"
    
    keyboard = [
        # بخش ویزارد داینامیک
        [InlineKeyboardButton(_bl("btn_custom_package", lang), callback_data="noop")],
        [InlineKeyboardButton(_bl("label_volume", lang), callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:gb_dec"),
            InlineKeyboardButton(f"{gb}{_bl('unit_gb', lang)}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:gb_inc")
        ],
        [InlineKeyboardButton(_bl("label_time", lang), callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:month_dec"),
            InlineKeyboardButton(f"{days}{_bl('unit_months', lang)}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:month_inc")
        ],
        [
            InlineKeyboardButton(f"{_bl('label_discount', lang)}{off_percent}%", callback_data="noop"),
            InlineKeyboardButton(f"{_bl('label_price', lang)}{price_str}", callback_data="noop")
        ],
        [InlineKeyboardButton(_bl("btn_buy_custom", lang), callback_data=f"buy:confirm_dyn:{server_id}")],
        
        # بخش پلن‌های آماده - دکمه هدایت به منوی اصلی
        [InlineKeyboardButton(_bl("btn_ready_plans", lang), callback_data=f"wiz:{server_id}:show_fixed")]
    ]
    
    # دیگر پلن‌های آماده را نمایش نمی‌دهیم، فقط دکمه هدایت به منوی اصلی
    
    keyboard.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="buy:back_main")])
    
    return InlineKeyboardMarkup(keyboard)

# --- تایید پرداخت (عکس آخر) ---
def confirm_payment_keyboard(lang: str = "fa"):
    return ReplyKeyboardMarkup([
        [KeyboardButton(i18n.t("btn_pay_done", lang))],
        [KeyboardButton(i18n.t("btn_back", lang))]
    ], resize_keyboard=True)
# این توابع را به فایل keyboards.py اضافه کنید

def category_keyboard(categories, server_id, lang: str = "fa"):
    rows = []
    # مرتب‌سازی بر اساس priority
    for cat in sorted(categories, key=lambda x: x.get('priority', 0)):
        rows.append([InlineKeyboardButton(cat.get('title', ''), callback_data=f"buy:cat:{server_id}:{cat.get('id', '')}")])
    rows.append([InlineKeyboardButton(_bl("btn_back_to_locations", lang), callback_data="buy:back_main")])
    return InlineKeyboardMarkup(rows)

def plans_keyboard(
    plans,
    server_id,
    cat_id,
    columns: int = 1,
    *,
    unlimited_volume: bool = False,
    unlimited_volume_from: int = 1000,
    unlimited_time: bool = False,
    unlimited_time_from: int = 365,
    sort_by_priority: bool = True,
    back_to_categories: bool = True,
    rtl_rows: bool = True,
    lang: str = "fa",
):
    rows = []
    cols = int(columns) if str(columns).isdigit() else 1
    if cols not in {1, 2}:
        cols = 1
    btns = []
    ordered_plans = sorted(plans, key=lambda x: x.get('priority', 0)) if sort_by_priority else list(plans)
    for p in ordered_plans:
        price_str = f"{p['price']:,}{_bl('unit_toman', lang)}"
        try:
            gb_val = float(p.get("gb") or 0)
        except (TypeError, ValueError):
            gb_val = 0.0
        try:
            days_val = int(p.get("days") or 0)
        except (TypeError, ValueError):
            days_val = 0
        vol_txt = _bl("unit_unlimited", lang) if (unlimited_volume and gb_val >= int(unlimited_volume_from)) else f"{gb_val:g}{_bl('unit_gb_v2', lang)}"
        day_txt = _bl("unit_unlimited", lang) if (unlimited_time and days_val >= int(unlimited_time_from)) else f"{days_val}{_bl('unit_days_v2', lang)}"
        btn_text = f"{p['title']} | {vol_txt} | {day_txt} - {price_str}"
        btns.append(InlineKeyboardButton(btn_text, callback_data=f"buy:plan:{server_id}:{p['id']}"))
    for i in range(0, len(btns), cols):
        chunk = btns[i:i + cols]
        rows.append(list(reversed(chunk)) if rtl_rows else chunk)
    if back_to_categories:
        rows.append([InlineKeyboardButton(_bl("btn_back_to_categories", lang), callback_data=f"buy:loc:{server_id}")])
    else:
        rows.append([InlineKeyboardButton(_bl("btn_back3", lang), callback_data="buy:back_main")])
    return InlineKeyboardMarkup(rows)

def confirm_buy_keyboard(server_id, plan_id, lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_confirm_pay", lang), callback_data=f"buy:confirm:{server_id}:{plan_id}")],
        [InlineKeyboardButton(_bl("btn_back3", lang), callback_data="buy:back_main")]
    ])


def selected_plan_keyboard(server_id: int, gb: int, days: int, price: int, plan_id: int = 0, lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_pay_direct", lang), callback_data=f"buy:pay_direct:{server_id}:{gb}:{days}:{price}:{int(plan_id or 0)}")],
        [InlineKeyboardButton(_bl("btn_pay_wallet", lang), callback_data=f"buy:pay_wallet:{server_id}:{gb}:{days}:{price}:{int(plan_id or 0)}")],
        [InlineKeyboardButton(_bl("btn_back_inline", lang), callback_data=f"buy:loc:{server_id}")],
    ])


def wallet_inline_keyboard(
    show_coupon: bool = True,
    *,
    show_card: bool = True,
    show_zarinpal: bool = False,
    show_perfect_money: bool = False,
    show_crypto: bool = False,
    lang: str = "fa",
):
    rows = []
    if show_card:
        rows.append([InlineKeyboardButton(_bl("btn_pay_card", lang), callback_data="wallet:card")])
    if show_zarinpal:
        rows.append([InlineKeyboardButton(_bl("btn_pay_gateway", lang), callback_data="wallet:zarinpal")])
    if show_perfect_money:
        rows.append([InlineKeyboardButton(_bl("btn_pay_perfectmoney", lang), callback_data="wallet:perfect")])
    if show_crypto:
        rows.append([InlineKeyboardButton(_bl("btn_pay_crypto", lang), callback_data="wallet:crypto")])
    if show_coupon:
        rows.append([InlineKeyboardButton(_bl("btn_apply_gift_coupon", lang), callback_data="wallet:coupon")])
    rows.append([InlineKeyboardButton(_bl("btn_back_inline", lang), callback_data="wallet:back")])
    return InlineKeyboardMarkup(rows)


def confirm_payment_inline_keyboard(lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_pay_done_inline", lang), callback_data="pay:receipt_done")],
        [InlineKeyboardButton(_bl("btn_back3", lang), callback_data="pay:cancel")],
    ])


def cancel_inline_keyboard(lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_back3", lang), callback_data="pay:cancel")],
    ])
# این دو تابع را به UserBot/keyboards.py اضافه کنید

def mixed_mode_keyboard(server_id, lang: str = "fa"):
    """منوی انتخاب بین بسته‌های ثابت و پویا در حالت ترکیبی"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_fixed_packages", lang), callback_data=f"buy:mixed:fixed:{server_id}")],
        [InlineKeyboardButton(_bl("btn_custom_wizard", lang), callback_data=f"buy:mixed:dyn:{server_id}")],
        [InlineKeyboardButton(_bl("btn_back_to_locations", lang), callback_data="buy:back_main")]
    ])

def buy_wizard_keyboard(server_id, gb, months, price, off_percent=0, lang: str = "fa"):
    """ویزارد خرید بسته دلخواه (پویا) با دکمه‌های مثبت و منفی"""
    price_str = f"{price:,}"
    keyboard = [
        [InlineKeyboardButton(_bl("label_volume", lang), callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:gb_dec"),
            InlineKeyboardButton(f"{gb}{_bl('unit_gb', lang)}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:gb_inc")
        ],
        [InlineKeyboardButton(_bl("label_time", lang), callback_data="noop")],
        [
            InlineKeyboardButton("➖", callback_data=f"wiz:{server_id}:month_dec"),
            InlineKeyboardButton(f"{months}{_bl('unit_months', lang)}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"wiz:{server_id}:month_inc")
        ],
        [
            InlineKeyboardButton(f"{_bl('label_discount', lang)}{off_percent}%", callback_data="noop"),
            InlineKeyboardButton(f"{_bl('label_price', lang)}{price_str}", callback_data="noop")
        ],
        [InlineKeyboardButton(_bl("btn_confirm_buy", lang), callback_data=f"buy:confirm_dyn:{server_id}")],
        [InlineKeyboardButton(_bl("btn_back3", lang), callback_data="buy:back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def subscription_status_keyboard(
    service_id=None,
    *,
    show_direct_config: bool = True,
    show_sub_link: bool = True,
    show_configs: bool = False,
    show_detach: bool = False,
    lang: str = "fa",
):
    """
    کیبورد وضعیت اشتراک مطابق UI جدید:
    - کانفیگ‌ها
    - تمدید اشتراک
    - تغییر نام اشتراک
    - تغییر لینک اشتراک
    - جداسازی اشتراک (فقط برای اشتراک‌های متصل‌شده دستی)
    """
    keyboard = [
        [InlineKeyboardButton(_bl("btn_configs", lang), callback_data=f"status:configs:{service_id}")],
        [InlineKeyboardButton(_bl("btn_renew_sub2", lang), callback_data=f"status:renew:{service_id}")],
        [InlineKeyboardButton(_bl("btn_rename_sub", lang), callback_data=f"status:rename:{service_id}")],
        [
            InlineKeyboardButton(
                _bl("btn_change_link", lang),
                callback_data=f"status:replace_link:{service_id}",
                style="danger",
            )
        ],
    ]
    if show_detach:
        keyboard.append([InlineKeyboardButton(_bl("btn_detach_sub", lang), callback_data=f"status:detach:{service_id}")])
    return InlineKeyboardMarkup(keyboard)


def replace_subscription_link_confirm_keyboard(service_id=None, lang: str = "fa"):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                _bl("btn_confirm_link_change", lang),
                callback_data=f"status:replace_link:{service_id}:confirm",
                style="danger",
            )
        ],
        [InlineKeyboardButton(_bl("btn_back3", lang), callback_data=f"status:menu:{service_id}")],
    ])


def direct_configs_keyboard(
    service_id=None,
    *,
    show_vless: bool = True,
    show_vmess: bool = True,
    show_trojan: bool = True,
    lang: str = "fa",
):
    rows = []
    if show_vless:
        rows.append([InlineKeyboardButton("Vless", callback_data=f"status:directcfg:{service_id}:vless")])
    if show_vmess:
        rows.append([InlineKeyboardButton("Vmess", callback_data=f"status:directcfg:{service_id}:vmess")])
    if show_trojan:
        rows.append([InlineKeyboardButton("Trojan", callback_data=f"status:directcfg:{service_id}:trojan")])
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data=f"status:menu:{service_id}")])
    return InlineKeyboardMarkup(rows)


def subscription_links_keyboard(service_id=None, lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_connect_guide", lang), callback_data=f"status:guide:{service_id}")],
        [InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data=f"status:menu:{service_id}")],
    ])


def invite_banner_keyboard(lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_bl("btn_my_invite_link", lang), callback_data="invite:get_banner")],
        [
            InlineKeyboardButton(_bl("btn_my_rewards", lang), callback_data="invite:rewards"),
            InlineKeyboardButton(_bl("btn_my_invites", lang), callback_data="invite:list"),
        ],
        [
            InlineKeyboardButton(_bl("btn_invite_stats", lang), callback_data="invite:stats"),
            InlineKeyboardButton(_bl("btn_rewards_history", lang), callback_data="invite:history"),
        ],
    ])


def force_join_keyboard(join_url: str = "", lang: str = "fa"):
    rows = []
    if str(join_url or "").strip():
        rows.append([InlineKeyboardButton(_bl("btn_join_channel", lang), url=join_url)])
    rows.append([InlineKeyboardButton(_bl("btn_check_join", lang), callback_data="forcejoin:check")])
    return InlineKeyboardMarkup(rows)


def guide_os_keyboard(back_token: str = "m", lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(i18n.t("guide_android", lang), callback_data=f"guide:android:{back_token}")],
        [InlineKeyboardButton(i18n.t("guide_ios", lang), callback_data=f"guide:ios:{back_token}")],
        [InlineKeyboardButton(i18n.t("guide_windows", lang), callback_data=f"guide:windows:{back_token}")],
        [InlineKeyboardButton(i18n.t("guide_mac", lang), callback_data=f"guide:mac:{back_token}")],
        [InlineKeyboardButton(i18n.t("guide_linux", lang), callback_data=f"guide:linux:{back_token}")],
        [InlineKeyboardButton(i18n.t("back", lang), callback_data=f"guide:back:{back_token}")],
    ])


def subscription_configs_keyboard(
    service_id=None,
    *,
    show_direct_config: bool = True,
    show_sub_link: bool = True,
    show_auto_sub_link: bool = False,
    show_sub_link_b64: bool = False,
    show_multi_server: bool = False,
    show_multi_server_b64: bool = False,
    lang: str = "fa",
):
    rows = []
    if show_direct_config:
        rows.append([InlineKeyboardButton(_bl("btn_direct_config", lang), callback_data=f"status:direct:{service_id}")])
    if show_sub_link:
        rows.append([InlineKeyboardButton(_bl("btn_sub_link", lang), callback_data=f"status:sub_link:{service_id}")])
    if show_auto_sub_link:
        rows.append([InlineKeyboardButton(_bl("btn_auto_sub", lang), callback_data=f"status:auto_sub:{service_id}")])
    if show_sub_link_b64:
        rows.append([InlineKeyboardButton(_bl("btn_sub_b64", lang), callback_data=f"status:sub_b64:{service_id}")])
    if show_multi_server:
        rows.append([InlineKeyboardButton(_bl("btn_smart_link", lang), callback_data=f"status:multi:{service_id}")])
    if show_multi_server_b64:
        rows.append([InlineKeyboardButton(_bl("btn_smart_link_b64", lang), callback_data=f"status:multi_b64:{service_id}")])

    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data=f"status:menu:{service_id}")])
    return InlineKeyboardMarkup(rows)


def services_list_keyboard(services, lang: str = "fa"):
    rows = []
    for s in services:
        name = (s.get("name") or "").strip() or f"{_bl('unit_service', lang)}{s.get('id')}"
        sid = int(s.get("id") or 0)
        if sid <= 0:
            continue
        rows.append([InlineKeyboardButton(name, callback_data=f"status:list:{sid}")])
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="status:list_back:0")])
    return InlineKeyboardMarkup(rows)


def renew_services_keyboard(services, lang: str = "fa"):
    rows = []
    for s in services:
        name = (s.get("name") or "").strip() or f"{_bl('unit_service', lang)}{s.get('id')}"
        sid = int(s.get("id") or 0)
        if sid <= 0:
            continue
        rows.append([InlineKeyboardButton(name, callback_data=f"renew:svc:{sid}")])
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="renew:back:0")])
    return InlineKeyboardMarkup(rows)


def support_panel_keyboard(lang: str = "fa"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(i18n.t("menu_faq", lang), callback_data="support:faq")],
        [InlineKeyboardButton(i18n.t("support_my_tickets", lang), callback_data="support:my:1")],
        [InlineKeyboardButton(i18n.t("support_new_ticket", lang), callback_data="support:new")],
        [InlineKeyboardButton(i18n.t("back", lang), callback_data="support:back_main")],
    ])


def ticket_skip_screenshot_keyboard(mode: str = "new", lang: str = "fa"):
    flow = str(mode or "new").strip().lower()
    if flow not in {"new", "reply"}:
        flow = "new"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_bl("btn_skip", lang), callback_data=f"support:{flow}:skip")],
            [InlineKeyboardButton(_bl("btn_cancel_inline", lang), callback_data=f"support:{flow}:cancel")],
        ]
    )


def ticket_confirm_keyboard(mode: str = "new", lang: str = "fa"):
    flow = str(mode or "new").strip().lower()
    if flow not in {"new", "reply"}:
        flow = "new"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_bl("btn_send", lang), callback_data=f"support:{flow}:send"),
                InlineKeyboardButton(_bl("btn_edit", lang), callback_data=f"support:{flow}:edit"),
            ],
            [InlineKeyboardButton(_bl("btn_cancel_inline", lang), callback_data=f"support:{flow}:cancel")],
        ]
    )


def user_tickets_list_keyboard(tickets, page: int, total_pages: int, lang: str = "fa"):
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
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="support:menu")])
    return InlineKeyboardMarkup(rows)


def user_ticket_detail_keyboard(ticket_code: int, can_reply: bool = True, is_closed: bool = False, lang: str = "fa"):
    rows = []
    if can_reply:
        rows.append([InlineKeyboardButton(_bl("btn_reply_ticket2", lang), callback_data=f"support:reply:{int(ticket_code)}")])
        rows.append([InlineKeyboardButton(_bl("btn_close_ticket", lang), callback_data=f"support:close:{int(ticket_code)}")])
    rows.append([InlineKeyboardButton(_bl("btn_back_inline2", lang), callback_data="support:menu")])
    return InlineKeyboardMarkup(rows)
