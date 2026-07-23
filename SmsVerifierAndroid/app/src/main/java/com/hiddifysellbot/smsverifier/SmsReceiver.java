package com.hiddifysellbot.smsverifier;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.telephony.SmsMessage;

public final class SmsReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !"android.provider.Telephony.SMS_RECEIVED".equals(intent.getAction())) {
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
}
