package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.database.Cursor;
import android.net.Uri;

public final class SmsInboxScanner {
    private static final int DEFAULT_LIMIT = 40;
    private static final long DEFAULT_MAX_AGE_MS = 7L * 24L * 60L * 60L * 1000L;

    private SmsInboxScanner() {
    }

    public static int scanRecent(Context context) {
        Context appContext = context.getApplicationContext();
        long cutoff = System.currentTimeMillis() - DEFAULT_MAX_AGE_MS;
        int scanned = 0;

        HistoryStore.add(
                appContext,
                "INBOX_SCAN_START",
                "Scanning last " + DEFAULT_LIMIT + " inbox SMS from recent 7 days."
        );

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
                HistoryStore.add(appContext, "INBOX_SCAN_FAILED", "SMS inbox cursor is null.");
                return 0;
            }

            int addressIndex = cursor.getColumnIndex("address");
            int bodyIndex = cursor.getColumnIndex("body");
            int dateIndex = cursor.getColumnIndex("date");
            while (cursor.moveToNext() && scanned < DEFAULT_LIMIT) {
                String sender = addressIndex >= 0 ? cursor.getString(addressIndex) : "";
                String body = bodyIndex >= 0 ? cursor.getString(bodyIndex) : "";
                long date = dateIndex >= 0 ? cursor.getLong(dateIndex) : System.currentTimeMillis();
                SmsProcessor.handleIncomingSms(appContext, sender, body, date);
                scanned++;
            }

            HistoryStore.add(appContext, "INBOX_SCAN_DONE", "Scanned=" + scanned);
            return scanned;
        } catch (SecurityException e) {
            HistoryStore.add(appContext, "INBOX_SCAN_FAILED", "READ_SMS permission denied.");
            return scanned;
        } catch (Exception e) {
            HistoryStore.add(appContext, "INBOX_SCAN_FAILED", e.getClass().getSimpleName() + ": " + e.getMessage());
            return scanned;
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
    }
}
