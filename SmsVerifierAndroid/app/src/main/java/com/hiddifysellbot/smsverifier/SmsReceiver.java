package com.hiddifysellbot.smsverifier;

import android.content.BroadcastReceiver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Telephony;
import android.telephony.SmsMessage;

public final class SmsReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        // SMS_RECEIVED: برای اپ‌های غیر پیش‌فرض
        // SMS_DELIVER: وقتی این اپ «اپ پیش‌فرض پیامک» است (لازم برای اندروید ۱۲/۱۳)
        // بدون SMS_DELIVER اپ بعد از default شدن هیچ پیامکی دریافت نمی‌کرد
        String action = intent == null ? "" : intent.getAction();
        boolean isReceived = "android.provider.Telephony.SMS_RECEIVED".equals(action);
        boolean isDeliver = "android.provider.Telephony.SMS_DELIVER".equals(action);
        if (!isReceived && !isDeliver) {
            return;
        }

        Bundle extras = intent.getExtras();
        if (extras == null) {
            return;
        }

        Object[] pdus = (Object[]) extras.get("pdus");
        if (pdus == null || pdus.length == 0) {
            return;
        }

        String format = extras.getString("format");
        String sender = "";
        StringBuilder body = new StringBuilder();
        long receivedAt = System.currentTimeMillis();

        for (Object pdu : pdus) {
            SmsMessage message;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                message = SmsMessage.createFromPdu((byte[]) pdu, format);
            } else {
                message = SmsMessage.createFromPdu((byte[]) pdu);
            }
            if (message == null) {
                continue;
            }
            if (sender.isEmpty()) {
                sender = message.getDisplayOriginatingAddress();
            }
            body.append(message.getMessageBody());
            receivedAt = message.getTimestampMillis() > 0 ? message.getTimestampMillis() : receivedAt;
        }

        // وقتی اپ پیش‌فرض پیامک است، سیستم پیامک را خودش در صندوق ذخیره
        // نمی‌کند (مسئول ذخیره اپ پیش‌فرض است) — پس خودمان می‌نویسیم تا
        // اسکنر صندوق داخل اپ هم پیامک‌های جدید را ببیند
        if (isDeliver) {
            storeSmsToInbox(context, sender, body.toString(), receivedAt);
        }

        final PendingResult pendingResult = goAsync();
        final Context appContext = context.getApplicationContext();
        final String finalSender = sender;
        final String finalBody = body.toString();
        final long finalReceivedAt = receivedAt;
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    SmsProcessor.handleIncomingSms(appContext, finalSender, finalBody, finalReceivedAt);
                } finally {
                    pendingResult.finish();
                }
            }
        }, "sellbot-sms-processor").start();
    }

    private static void storeSmsToInbox(Context context, String sender, String body, long receivedAt) {
        try {
            ContentValues values = new ContentValues();
            values.put(Telephony.Sms.ADDRESS, sender);
            values.put(Telephony.Sms.BODY, body);
            values.put(Telephony.Sms.DATE, receivedAt);
            values.put(Telephony.Sms.READ, 1);
            values.put(Telephony.Sms.SEEN, 1);
            values.put(Telephony.Sms.TYPE, Telephony.Sms.MESSAGE_TYPE_INBOX);
            context.getContentResolver().insert(Uri.parse("content://sms/inbox"), values);
        } catch (Exception ignored) {
            // ذخیره در صندوق حیاتی نیست؛ پردازش وب‌هوک مستقل از آن ادامه می‌یابد
        }
    }
}
