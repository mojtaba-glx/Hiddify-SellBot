# Hiddify-SellBot

Telegram AdminBot + UserBot for Hiddify sales workflows.

## Quick Start (Menu Mode)

```bash
git clone https://github.com/mojtaba-glx/Hiddify-SellBot.git && cd Hiddify-SellBot && chmod +x install.sh && ./install.sh
```

`./install.sh` with no command opens the interactive menu:

```text
1) install  2) update  3) start  4) stop  5) restart ...
```

## First-Time Install (Direct Command)

```bash
cd ~/Hiddify-SellBot && ./install.sh install
```

## One-Line Update (after first install)

```bash
cd ~/Hiddify-SellBot && ./install.sh update
```

## Main Commands

```bash
./install.sh install       # First-time setup
./install.sh update        # Safe backup + git update + restart
./install.sh start         # Start bots
./install.sh stop          # Stop bots
./install.sh restart       # Restart bots
./install.sh status        # Check status
./install.sh config        # Configure .env interactively
./install.sh uninstall     # Remove bot runtime/data from this folder
./install.sh factory-reset # Reset bot data to factory defaults
./install.sh version       # Show current version
```

## Notes

- Dependencies are installed automatically from `requirements.txt`.
- `.env` is required (`ADMIN_ID`, `ADMIN_BOT_TOKEN`, `USER_BOT_TOKEN`).
- `install` asks for tokens only on first setup (when `.env` does not exist).
- `update` never asks for tokens and only updates code/dependencies then restarts bots.
- `factory-reset` does not modify code or `.env`; it only resets runtime data.
- `uninstall` removes runtime/data files (`.env`, `venv`, `logs`, `backups`, `Receiptions`, DB/data files) and keeps source code.


## Hiddify-SellBot v2.0.0

این نسخه یک به‌روزرسانی Major است با تمرکز روی پایداری عملیاتی، نودسازی کامل، مدیریت دقیق اشتراک‌ها و رفع باگ‌های بحرانی.

## تغییرات اصلی
- تکمیل منطق نودسازی برای همگام‌سازی اشتراک بین سرور اصلی و نودها.
- اصلاح مدیریت کاربر روی خوشه: فعال/غیرفعال‌سازی روی همه نودهای مرتبط.
- اصلاح ویرایش اشتراک: تغییر حجم/زمان/نام/یادداشت روی سرور اصلی و نودها به‌صورت یکپارچه.
- بهبود Enforcer سراسری برای کنترل مصرف تجمیعی و قطع خودکار سرویس‌های خارج از محدودیت.
- بهبود نمایش و مدیریت سرویس‌ها در UserBot هنگام قطعی موقت پنل‌ها.
- سرویس‌های حذف‌شده از پنل بعد از TTL پاک می‌شوند (پیش‌فرض جدید: 7 روز).
- هنگام حذف سرور از AdminBot، دیتای سرویس‌های وابسته به آن سرور از دیتابیس ربات هم پاک می‌شود.
- بازطراحی و بهینه‌سازی بخش تیکت‌ها (ادمین/کاربر)، جریان پاسخ‌دهی و مدیریت وضعیت تیکت.
- بهبود مسیرهای کانفیگ و لینک اشتراک، شامل استخراج و نمایش بهتر کانفیگ‌ها.
- بهینه‌سازی منوها و تجربه کاربری در بخش جستجو، مدیریت کاربران و تنظیمات.

## بکاپ/ریستور
- تثبیت فرآیند بکاپ و بازیابی برای ساختار فعلی پروژه.
- پشتیبانی از بکاپ کامل ربات با ساختار قابل بازیابی.
- بهبود رفتار زمان‌بندی و مدیریت نگهداری بکاپ‌ها.

## امنیت و انتشار
- پاکسازی اطلاعات حساس از محیط انتشار.
- آماده‌سازی پروژه برای ریلیز تمیز در GitHub.
- بهبود اسکریپت نصب/به‌روزرسانی/مدیریت سرویس.

## متغیر محیطی جدید
- `USERBOT_MISSING_SERVICE_DELETE_DAYS` (اختیاری، پیش‌فرض: `7`)

## نسخه
- Version: `2.0.0`
- نوع انتشار: Major Release
