package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.database.Cursor;
import android.net.Uri;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public final class SmsInboxScanner {
    private static final int PROCESS_LIMIT = 15;
    private static final int LOOKUP_LIMIT = 80;
    private static final long DEFAULT_MAX_AGE_MS = 7L * 24L * 60L * 60L * 1000L;

    private SmsInboxScanner() {
    }

    public static int scanRecent(Context context) {
        Context appContext = context.getApplicationContext();
        long cutoff = System.currentTimeMillis() - DEFAULT_MAX_AGE_MS;
        int checked = 0;
        int processed = 0;
        List<SmsCandidate> candidates = new ArrayList<>();
        Set<String> currentInboxEventIds = new HashSet<>();

        Cursor cursor = null;
        try {
            cursor = appContext.getContentResolver().query(
                    Uri.parse("content://sms/inbox"),
                    new String[]{"address", "body", "date"},
                    "date >= ?",
                    new String[]{String.valueOf(cutoff)},
                    "date DESC"
            );
            if (cursor == null) {
                HistoryStore.add(appContext, "INBOX_SCAN_FAILED", "امکان خواندن صندوق پیامک وجود ندارد.");
                return 0;
            }

            int addressIndex = cursor.getColumnIndex("address");
            int bodyIndex = cursor.getColumnIndex("body");
            int dateIndex = cursor.getColumnIndex("date");
            while (cursor.moveToNext() && checked < LOOKUP_LIMIT && processed < PROCESS_LIMIT) {
                String sender = addressIndex >= 0 ? cursor.getString(addressIndex) : "";
                String body = bodyIndex >= 0 ? cursor.getString(bodyIndex) : "";
                long date = dateIndex >= 0 ? cursor.getLong(dateIndex) : System.currentTimeMillis();
                checked++;
                if (!SmsProcessor.isConfiguredBankIncomingPayment(appContext, sender, body)) {
                    continue;
                }
                long amount = PaymentSmsParser.extractAmount(body);
                String reference = PaymentSmsParser.extractReference(body);
                String eventId = PaymentSmsParser.buildEventId(sender, body, amount, reference);
                if (!eventId.isEmpty()) {
                    currentInboxEventIds.add(eventId);
                }
                candidates.add(new SmsCandidate(sender, body, date, eventId));
            }

            HistoryStore.syncBankSmsWithInbox(appContext, currentInboxEventIds);

            for (SmsCandidate candidate : candidates) {
                if (processed >= PROCESS_LIMIT) {
                    break;
                }
                if (!candidate.eventId.isEmpty()
                        && HistoryStore.containsUnique(appContext, candidate.eventId)
                        && !HistoryStore.shouldRetryUnique(appContext, candidate.eventId)) {
                    continue;
                }
                SmsProcessor.handleIncomingSms(appContext, candidate.sender, candidate.body, candidate.date);
                processed++;
            }

            HistoryStore.add(
                    appContext,
                    "INBOX_SCAN_DONE",
                    "همگام‌سازی صندوق پیامک انجام شد."
                            + "\nپیامک‌های بانکی موجود: " + candidates.size()
                            + "\nپیامک‌های جدید پردازش‌شده: " + processed
                            + "\nپیامک‌های بررسی‌شده: " + checked
            );
            return processed;
        } catch (SecurityException e) {
            HistoryStore.add(appContext, "INBOX_SCAN_FAILED", "اجازه READ_SMS داده نشده است. از تنظیمات گوشی مجوز خواندن SMS را فعال کن.");
            return processed;
        } catch (Exception e) {
            HistoryStore.add(appContext, "INBOX_SCAN_FAILED", "خطا هنگام خواندن پیامک‌ها: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            return processed;
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
    }

    private static final class SmsCandidate {
        final String sender;
        final String body;
        final long date;
        final String eventId;

        SmsCandidate(String sender, String body, long date, String eventId) {
            this.sender = sender == null ? "" : sender;
            this.body = body == null ? "" : body;
            this.date = date;
            this.eventId = eventId == null ? "" : eventId;
        }
    }
}
