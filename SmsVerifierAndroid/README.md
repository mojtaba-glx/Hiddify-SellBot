# SellBot SMS Verifier

اپ اندروید ساده برای تایید هوشمند کارت‌به‌کارت در Hiddify-SellBot.

## کارکرد

- دریافت SMSهای جدید روی گوشی
- بررسی فقط سرشماره‌های مجاز بانک
- استخراج مبلغ، واحد پول و کد/شماره پیگیری
- فیلتر اختیاری چهار رقم آخر کارت
- ارسال نتیجه به Webhook ربات با `Secret Key`
- ثبت گزارش SMSهای ارسال‌شده، خطاخورده یا ردشده داخل خود اپ

## امنیت

- اپ توکن تلگرام نمی‌گیرد و مستقیم به Telegram وصل نمی‌شود.
- همه درخواست‌ها به endpoint اختصاصی ربات ارسال می‌شود.
- هر درخواست با هدر `X-SellBot-Sms-Secret` و `X-SellBot-Event-Id` فرستاده می‌شود.
- اگر سرشماره بانک تنظیم نشده باشد، هیچ SMSای ارسال نمی‌شود.
- مجوز اپ فقط برای دریافت SMSهای جدید است؛ SMSهای قدیمی گوشی خوانده نمی‌شوند.

## تنظیمات داخل اپ

1. `فعال‌سازی پردازش خودکار SMS` را روشن کنید.
2. `Webhook URL ربات` را وارد کنید.
3. `Secret Key اتصال` را وارد کنید.
4. `سرشماره‌های مجاز بانک` را با کاما یا خط جدید وارد کنید.
5. اگر بانک در پیامک چهار رقم آخر کارت را می‌فرستد، گزینه فیلتر چهار رقم کارت را روشن کنید.
6. اگر بانک چهار رقم کارت را نمی‌فرستد، این گزینه را خاموش بگذارید.

## نمونه Payload

```json
{
  "event_id": "sha256...",
  "source": "android_sms",
  "sender": "BANK",
  "body": "متن SMS بانک",
  "amount": 100000,
  "currency": "toman",
  "reference": "123456",
  "card_last4": "1234",
  "card_last4_required": true,
  "test": false,
  "received_at": 1780000000000,
  "device_time": 1780000001000
}
```

## Headerهای ارسالی

```text
Content-Type: application/json; charset=utf-8
User-Agent: SellBotSmsVerifier/1.0
X-SellBot-Sms-Secret: <SECRET>
X-SellBot-Event-Id: <EVENT_ID>
```

## ساخت APK

با Android Studio:

1. پوشه `SmsVerifierAndroid` را باز کنید.
2. صبر کنید Gradle Sync کامل شود.
3. از منوی `Build > Build Bundle(s) / APK(s) > Build APK(s)` خروجی APK بگیرید.

با ترمینال روی سیستمی که Java و Android SDK دارد:

```bash
cd SmsVerifierAndroid
gradle assembleDebug
```

خروجی:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## پیشنهاد اتصال به ربات

در سمت ربات بهتر است اول حالت گزارش فعال باشد و تایید خودکار بعد از تست چند SMS واقعی روشن شود:

- دریافت `test=true` فقط برای تست اتصال
- رد کردن event تکراری با `event_id`
- تطبیق مبلغ با پرداخت‌های pending
- محدود کردن اختلاف زمان SMS با سفارش
- ثبت گزارش تایید/رد در پنل ادمین
