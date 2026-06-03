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
            HistoryStore.add(context, "SKIPPED", "Sender filters are empty. SMS was not sent.");
            return;
        }
        if (!settings.matchesSender(sender)) {
            return;
        }
        if (!settings.canSendWebhook()) {
            HistoryStore.add(context, "SKIPPED", "Webhook URL/secret is empty. Sender=" + sender);
            return;
        }

        String cardLast4 = settings.getCardLast4();
        if (settings.isCardLast4Enabled()) {
            if (cardLast4.length() != 4) {
                HistoryStore.add(context, "SKIPPED", "Card last 4 is enabled but not configured.");
                return;
            }
            if (!PaymentSmsParser.containsCardLast4(body, cardLast4)) {
                HistoryStore.add(context, "SKIPPED", "Card last 4 not found. Sender=" + sender);
                return;
            }
        }
        if (!PaymentSmsParser.isIncomingPayment(body)) {
            HistoryStore.add(context, "SKIPPED", "SMS is not an incoming payment. Sender=" + sender);
            return;
        }

        long amount = PaymentSmsParser.extractAmount(body);
        if (amount <= 0) {
            HistoryStore.add(context, "SKIPPED", "Amount not found. Sender=" + sender + "\nSMS: " + body);
            return;
        }
        String detectedCardLast4 = PaymentSmsParser.extractCardLast4(body);

        SmsEvent event = new SmsEvent();
        event.sender = sender == null ? "" : sender;
        event.body = body == null ? "" : body;
        event.amount = amount;
        event.currency = PaymentSmsParser.extractCurrency(body);
        event.reference = PaymentSmsParser.extractReference(body);
        event.cardLast4 = detectedCardLast4.length() == 4 ? detectedCardLast4 : cardLast4;
        event.cardLast4Required = settings.isCardLast4Enabled();
        event.receivedAt = receivedAt;
        event.deviceTime = System.currentTimeMillis();
        event.test = false;
        event.eventId = PaymentSmsParser.buildEventId(event.sender, event.body, event.amount, event.reference);

        WebhookClient.Result result = WebhookClient.post(settings.getWebhookUrl(), settings.getSecret(), event);
        String detail = "Sender=" + event.sender
                + "\nAmount=" + event.amount + " " + event.currency
                + "\nReference=" + event.reference
                + "\nEvent=" + event.eventId
                + "\nHTTP=" + result.statusCode
                + "\nResponse=" + (result.body == null ? "" : result.body)
                + "\nError=" + (result.error == null ? "" : result.error);
        HistoryStore.add(context, result.ok ? "SENT" : "FAILED", detail);
    }
}
