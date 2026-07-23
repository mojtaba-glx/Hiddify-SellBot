# راه‌اندازی Cloudflare Tunnel برای Hiddify-SellBot

این روش برای وقتی است که ربات و پنل Hiddify روی یک سرور هستند و نمی‌خواهیم با پورت‌های `80` و `443` پنل هیدیفای تداخل ایجاد شود.

در این روش SSL روی خود سرور نصب نمی‌شود؛ Cloudflare ترافیک HTTPS را می‌گیرد و از طریق Tunnel به سرویس داخلی ربات روی `127.0.0.1:8787` وصل می‌کند.

## نقشه اتصال

```text
کاربر / اپ / لینک ساب
        ↓ HTTPS
Cloudflare
        ↓ Tunnel
127.0.0.1:8787 روی سرور
        ↓
Hiddify-SellBot Sub Server
```

## پیش‌نیازها

- دامنه داخل Cloudflare باشد، مثل `example.com`
- زیردامنه‌ای برای ربات انتخاب شود، مثل `sell.example.com`
- ربات روی سرور نصب و اجرا شده باشد
- دسترسی `root` به سرور داشته باشید

## مرحله 1: تنظیم ربات برای Sub Server داخلی

روی سرور اصلی:

```bash
cd /root/Hiddify-SellBot
cp .env .env.bak-$(date +%F-%H%M)

sed -i '/^SUB_SERVER_/d' .env
cat >> .env <<'EOF_ENV'
SUB_SERVER_ENABLED=true
SUB_SERVER_HOST=127.0.0.1
SUB_SERVER_PORT=8787
SUB_SERVER_PUBLIC_HOST=sell.example.com
SUB_SERVER_PUBLIC_SCHEME=https
SUB_SERVER_PUBLIC_PORT=443
EOF_ENV

./install.sh restart
```

تست داخلی:

```bash
curl -s http://127.0.0.1:8787/sub/test.txt
```

اگر این پیام آمد یعنی سرویس داخلی سالم است:

```text
subscription token not found
```

## مرحله 2: نصب cloudflared

Ubuntu/Debian:

```bash
apt update
apt install -y curl gpg lsb-release
mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflared.list

apt update
apt install -y cloudflared
```

بررسی نصب:

```bash
cloudflared --version
```

## مرحله 3: ورود به Cloudflare

روی سرور:

```bash
cloudflared tunnel login
```

یک لینک می‌دهد. لینک را در مرورگر باز کنید، دامنه را انتخاب کنید و اجازه بدهید.

بعد از تایید، فایل مجوز در مسیر مشابه زیر ساخته می‌شود:

```text
/root/.cloudflared/cert.pem
```

## مرحله 4: ساخت Tunnel

```bash
cloudflared tunnel create sellbot-sub
```

خروجی یک `Tunnel ID` می‌دهد. نمونه:

```text
82aadb54-5a31-4781-9037-a8ace216cfcf
```

فایل credential هم ساخته می‌شود:

```text
/root/.cloudflared/82aadb54-5a31-4781-9037-a8ace216cfcf.json
```

## مرحله 5: ساخت فایل تنظیمات Tunnel

شناسه Tunnel را با شناسه خودت جایگزین کن:

```bash
mkdir -p /etc/cloudflared
cat >/etc/cloudflared/config.yml <<'EOF_CFG'
tunnel: 82aadb54-5a31-4781-9037-a8ace216cfcf
credentials-file: /root/.cloudflared/82aadb54-5a31-4781-9037-a8ace216cfcf.json

ingress:
  - hostname: sell.example.com
    service: http://127.0.0.1:8787
  - service: http_status:404
EOF_CFG
```

## مرحله 6: تنظیم DNS دامنه

روش پیشنهادی با دستور:

```bash
cloudflared tunnel route dns sellbot-sub sell.example.com
```

اگر خطای زیر آمد:

```text
An A, AAAA, or CNAME record with that host already exists
```

یعنی برای `sell.example.com` قبلاً رکورد ساخته شده. داخل Cloudflare DNS رکورد قبلی `A` یا `CNAME` مربوط به `sell` را حذف کن، سپس دوباره دستور بالا را بزن.

اگر خواستی دستی بسازی:

```text
Type: CNAME
Name: sell
Target: <TUNNEL_ID>.cfargotunnel.com
Proxy: ON / Orange Cloud
```

نمونه:

```text
82aadb54-5a31-4781-9037-a8ace216cfcf.cfargotunnel.com
```

نکته مهم: برای این روش رکورد `A` لازم نیست.

## مرحله 7: ساخت سرویس systemd

```bash
cat >/etc/systemd/system/cloudflared.service <<'EOF_SERVICE'
[Unit]
Description=cloudflared tunnel for Hiddify-SellBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable --now cloudflared
```

بررسی وضعیت:

```bash
systemctl status cloudflared --no-pager -l
```

باید `active (running)` باشد.

## مرحله 8: تست نهایی

```bash
curl -sk https://sell.example.com/sub/test.txt
```

اگر این پیام آمد، همه چیز درست است:

```text
subscription token not found
```

بعد داخل ربات کاربران لینک هوشمند بگیر. لینک باید شبیه این باشد:

```text
https://sell.example.com/sub/USER_UUID/all.txt
```

## تنظیم Webhook اپ SMS Verifier

وقتی Tunnel فعال شد، آدرس Webhook داخل اپ پیامکی این است:

```text
https://sell.example.com/payment/sms-webhook
```

Secret Key را از منوی ادمین ربات بگیر:

```text
پرداخت‌ها / کارت به کارت / تایید خودکار SMS / نمایش Secret برای اپ
```

## اگر سرور عوض شد

روی سرور جدید این کارها را انجام بده:

1. ربات را نصب کن و `.env` را تنظیم کن.
2. `SUB_SERVER_*` را مثل مرحله 1 بگذار.
3. `cloudflared` را نصب کن.
4. اگر همان Tunnel قبلی را می‌خواهی استفاده کنی، credential JSON همان Tunnel را به سرور جدید منتقل کن.
5. اگر Tunnel جدید ساختی، DNS دامنه را به Tunnel جدید وصل کن.
6. سرویس `cloudflared` را فعال کن.
7. تست کن:

```bash
curl -s http://127.0.0.1:8787/sub/test.txt
curl -sk https://sell.example.com/sub/test.txt
```

## دستورات عیب‌یابی سریع

وضعیت ربات:

```bash
cd /root/Hiddify-SellBot
./install.sh status
```

تست سرویس داخلی ربات:

```bash
curl -v http://127.0.0.1:8787/sub/test.txt
ss -ltnp | grep 8787
```

وضعیت Tunnel:

```bash
systemctl status cloudflared --no-pager -l
journalctl -u cloudflared -n 100 --no-pager
```

بررسی DNS:

```bash
dig +short @1.1.1.1 sell.example.com A
dig +short @1.1.1.1 sell.example.com CNAME
```

اگر خروجی `A` شبیه IPهای Cloudflare بود، مثل `188.114.x.x`، طبیعی است چون Proxy روشن است.

## نکات مهم امنیتی

- پورت `8787` را عمومی نکن؛ بهتر است فقط روی `127.0.0.1` گوش کند.
- روی سرور هیدیفای با این روش نیازی به Certbot/Nginx جدا برای دامنه ربات نیست.
- به تنظیمات Nginx/Haproxy هیدیفای دست نزن، مگر دقیقاً بدانی چه می‌کنی.
- از `.env` و دیتابیس قبل از تغییرات بکاپ بگیر.

## جمع‌بندی

این روش برای سرورهایی که پنل Hiddify روی همان سرور فعال است بهترین انتخاب است، چون:

- با پورت‌های Hiddify تداخل ندارد.
- SSL توسط Cloudflare انجام می‌شود.
- لینک‌های اشتراک و Webhook پیامکی با HTTPS کار می‌کنند.
- جابه‌جایی سرور راحت‌تر می‌شود.
