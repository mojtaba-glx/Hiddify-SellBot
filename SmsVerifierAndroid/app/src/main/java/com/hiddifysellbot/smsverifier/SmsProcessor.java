package com.hiddifysellbot.smsverifier;

import android.content.Context;

public final class SmsProcessor {
    private SmsProcessor() {
    }

    public static void handleIncomingSms(Context context, String sender, String body, long receivedAt) {
        SettingsStore settings = new SettingsStore(context);
        if (!settings.isEnabled()) {
            return;
        }
        if (!settings.hasSenderFilters()) {
            HistoryStore.add(context, "SKIPPED", "هیچ سرشماره بانکی تنظیم نشده است؛ SMS ارسال نشد.");
            return;
        }
        String configuredBankName = settings.getMatchedConfiguredBankName(sender, body);
        String bankName = configuredBankName.isEmpty() ? settings.getMatchedBankName(sender, body) : configuredBankName;
        if (bankName.isEmpty()) {
            if (PaymentSmsParser.isIncomingPayment(body)) {
                HistoryStore.add(
                        context,
                        "SKIPPED",
                        "پیامک شبیه واریز بود، اما با هیچ بانک فعالی تطبیق نشد.\n"
                                + "👤 سرشماره: " + sender
                                + "\n📄 متن SMS:\n" + body
                );
            }
            return;
        }
        if (!settings.canSendWebhook()) {
            HistoryStore.add(context, "SKIPPED", "Webhook URL یا Secret Key خالی است.\nفرستنده: " + sender);
            return;
        }

        if (!PaymentSmsParser.isIncomingPayment(body)) {
            return;
        }

        long amount = PaymentSmsParser.extractAmount(body);
        if (amount <= 0) {
            String eventId = PaymentSmsParser.buildEventId(sender, body, 0, "");
            HistoryStore.addUnique(
                    context,
                    "BANK_SKIPPED",
                    "مبلغ از داخل SMS پیدا نشد.\n"
                            + "🏦 بانک: " + bankName
                            + "\n👤 سرشماره: " + sender
                            + "\n🔐 شناسه داخلی: " + eventId
                            + "\n📄 متن SMS:\n" + body,
                    eventId
            );
            return;
        }
        String detectedCardLast4 = PaymentSmsParser.extractCardLast4(body);
        String currency = PaymentSmsParser.extractCurrency(body);

        SmsEvent event = new SmsEvent();
        event.sender = sender == null ? "" : sender;
        event.body = body == null ? "" : body;
        event.amount = amount;
        event.currency = currency;
        event.reference = PaymentSmsParser.extractReference(body);
        event.cardLast4 = settings.isCardLast4Enabled() && detectedCardLast4.length() == 4 ? detectedCardLast4 : "";
        event.cardLast4Required = false;
        event.receivedAt = receivedAt;
        event.deviceTime = System.currentTimeMillis();
        event.test = false;
        event.eventId = PaymentSmsParser.buildEventId(event.sender, event.body, event.amount, event.reference);

        WebhookClient.Result result = WebhookClient.post(settings.getWebhookUrl(), settings.getSecret(), event);
        String detail = "📨 نتیجه: " + WebhookClient.persianStatus(result)
                + "\n🏦 بانک: " + bankName
                + "\n👤 سرشماره: " + event.sender
                + "\n💰 مبلغ SMS: " + event.amount + " " + event.currency
                + "\n💵 معادل تقریبی: " + estimateToman(event.amount, event.currency) + " تومان"
                + "\n🔖 پیگیری: " + emptyDash(event.reference)
                + "\n💳 چهار رقم کارت: " + emptyDash(event.cardLast4)
                + "\n🌐 کد HTTP: " + result.statusCode
                + "\n🧾 پاسخ ربات: " + summarizeResponse(result.body)
                + "\n⚠️ خطا: " + emptyDash(result.error)
                + "\n🔐 شناسه داخلی: " + event.eventId
                + "\n📄 متن SMS:\n" + event.body;
        HistoryStore.addUnique(context, WebhookClient.statusLabel(result), detail, event.eventId);
    }

    private static String estimateToman(long amount, String currency) {
        String c = currency == null ? "" : currency;
        long toman = "rial".equals(c) || "irr".equals(c) ? Math.round(amount / 10.0) : amount;
        return String.valueOf(toman);
    }

    private static String emptyDash(String value) {
        return value == null || value.trim().isEmpty() ? "-" : value.trim();
    }

    private static String summarizeResponse(String body) {
        if (body == null || body.trim().isEmpty()) {
            return "-";
        }
        String text = body.trim();
        if (text.contains("\"status\":\"approved\"")) {
            return "تایید شد";
        }
        if (text.contains("\"status\":\"no_pending_match\"")) {
            return "پرداخت pending پیدا نشد";
        }
        if (text.contains("\"duplicate\":true")) {
            return "تکراری/قبلاً ثبت شده";
        }
        if (text.length() > 180) {
            return text.substring(0, 180) + "...";
        }
        return text;
    }
}
