# Hiddify-SellBot

Telegram AdminBot + UserBot for Hiddify sales workflows.

## Quick Start (Menu Mode)

```bash
git clone https://github.com/mojtaba-glx/Hiddify-SellBot.git && cd Hiddify-SellBot && chmod +x install.sh && ./install.sh
```

`./install.sh` with no command opens the interactive menu:

```text
install | update | update-force | reinstall | start | stop | restart | status | diag | logs ...
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
./install.sh update-force  # Force sync code to remote branch + restart
./install.sh reinstall     # Recreate venv + reinstall dependencies + restart
./install.sh start         # Start bots
./install.sh stop          # Stop bots
./install.sh restart       # Restart bots
./install.sh status        # Check status
./install.sh menu          # Open interactive panel menu
./install.sh panel         # Alias of menu
./install.sh diag          # Quick diagnostics (git/env/log snapshot)
./install.sh logs          # Live logs (AdminBot + UserBot)
./install.sh config        # Configure .env interactively
./install.sh ssl DOMAIN [EMAIL]  # Configure Nginx + Let's Encrypt SSL for Multi Server domain
./install.sh uninstall     # Remove bot runtime/data from this folder
./install.sh factory-reset # Reset bot data to factory defaults
./install.sh version       # Show current version
```

## SSL for Multi Server Links

If you want `https://` links for `Multi Server`, set DNS of your domain to this server IP, then run:

```bash
cd ~/Hiddify-SellBot && sudo ./install.sh ssl sell.example.com admin@example.com
```

This command:
- installs `nginx` + `certbot`
- issues Let's Encrypt certificate
- configures reverse proxy to `SUB_SERVER_PORT`
- sets `SUB_SERVER_PUBLIC_HOST/SCHEME/PORT` in `.env`

## Notes

- Dependencies are installed automatically from `requirements.txt`.
- `.env` is required (`ADMIN_ID`, `ADMIN_BOT_TOKEN`, `USER_BOT_TOKEN`).
- `install` asks for tokens only on first setup (when `.env` does not exist).
- `update` never asks for tokens and only updates code/dependencies then restarts bots.
- `factory-reset` does not modify code or `.env`; it only resets runtime data.
- `uninstall` removes runtime/data files (`.env`, `venv`, `logs`, `backups`, `Receiptions`, DB/data files) and keeps source code.

## Hiddify-SellBot v2.1.2

این نسخه یک Patch Release برای ساده‌سازی پنل مدیریت اسکریپت نصب/آپدیت است.

## تغییرات اصلی در v2.1.2
- بازطراحی منوی تعاملی `install.sh` با گزینه‌های کامل‌تر برای مدیریت روزانه.
- اضافه شدن دستورات جدید:
  - `./install.sh update-force`
  - `./install.sh reinstall`
  - `./install.sh panel`
  - `./install.sh diag`
  - `./install.sh logs`
- بهبود منطق `update`: اگر فقط فایل‌های runtime (مثل `servers.json`/`plans.json`/DB) تغییر کرده باشند، آپدیت دیگر بی‌دلیل skip نمی‌شود.
- اضافه شدن حالت تشخیصی سریع با گزارش وضعیت سرویس‌ها، env، git و snapshot لاگ‌ها.

## نسخه
- Version: `2.1.2`
- نوع انتشار: Patch Release

## Hiddify-SellBot v2.1.1

این نسخه یک Patch Release برای بهبود اعلان‌های یادآوری تمدید اشتراک است.

## تغییرات اصلی در v2.1.1
- اضافه شدن نام اشتراک داخل پیام یادآوری تمدید (روز و حجم).
- یکدست‌سازی قالب پیام‌های یادآوری تمدید برای خوانایی بهتر.
- تفکیک کلید ارسال یادآور برای هر اشتراک، تا در کاربران چنداشتراکی پیام هر سرویس جداگانه ارسال شود.

## نسخه
- Version: `2.1.1`
- نوع انتشار: Patch Release

## Hiddify-SellBot v2.1.0

این نسخه یک Minor Release برای پایداری عملیاتی، ابزار اشکال‌زدایی و رفع باگ‌های مهم مدیریت ربات است.

## تغییرات اصلی در v2.1.0
- اضافه شدن دستور `/debug` در AdminBot با گزارش کامل (وضعیت فایل‌ها، دیتابیس، jobها، شبکه و snapshot لاگ‌ها).
- رفع باگ مسیر تنظیم دامنه `Multi Server` که باعث پیام «گزینه انتخاب‌شده معتبر نیست» می‌شد.
- پشتیبانی کامل از ورود دامنه با و بدون `https://` در تنظیمات `Multi Server`.
- اضافه شدن راهنمای SSL داخل منوی تنظیمات لینک اشتراک.
- اضافه شدن دستور `./install.sh ssl DOMAIN [EMAIL]` برای راه‌اندازی SSL خودکار (Nginx + Certbot).
- رفع باگ پاس نشدن آرگومان‌های دستور `install.sh ssl`.
- رفع باگ حجم تست: پشتیبانی از مقادیر اعشاری زیر ۱ گیگ (`0.2`, `0.3`, `0.5`, `0.75`).
- رفع باگ ماندگاری کارت‌ها/تنظیمات در `servers.json` هنگام ذخیره‌سازی سرورها.

## نسخه
- Version: `2.1.0`
- نوع انتشار: Minor Release


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
