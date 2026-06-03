package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public final class HistoryStore {
    private static final String PREF_NAME = "sellbot_sms_history";
    private static final String KEY_HISTORY = "history";
    private static final String KEY_APPROVED_HISTORY = "approved_history";
    private static final String KEY_BANK_SMS_HISTORY = "bank_sms_history";
    private static final String SEP = "\n---\n";
    private static final int MAX_ITEMS = 80;
    private static final int MAX_APPROVED_ITEMS = 40;
    private static final int MAX_BANK_SMS_ITEMS = 80;

    private HistoryStore() {
    }

    public static void add(Context context, String status, String detail) {
        String entry = buildEntry(status, detail);
        save(context, KEY_HISTORY, entry, MAX_ITEMS);
        if ("APPROVED".equals(status) || "APPROVED_DUPLICATE".equals(status)) {
            save(context, KEY_APPROVED_HISTORY, entry, MAX_APPROVED_ITEMS);
        }
        if (isBankSmsStatus(status)) {
            save(context, KEY_BANK_SMS_HISTORY, entry, MAX_BANK_SMS_ITEMS);
        }
    }

    public static String get(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_HISTORY, "");
    }

    public static String getApproved(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_APPROVED_HISTORY, "");
    }

    public static String getBankSms(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_BANK_SMS_HISTORY, "");
    }

    public static void clear(Context context) {
        context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_HISTORY)
                .remove(KEY_APPROVED_HISTORY)
                .remove(KEY_BANK_SMS_HISTORY)
                .apply();
    }

    private static String buildEntry(String status, String detail) {
        String now = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date());
        return "🕒 " + now + "\n" + statusTitle(status) + "\n" + trim(detail, 900);
    }

    private static void save(Context context, String key, String entry, int maxItems) {
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String old = prefs.getString(key, "");
        String merged = old == null || old.isEmpty() ? entry : entry + SEP + old;
        String[] parts = merged.split(SEP);
        StringBuilder limited = new StringBuilder();
        for (int i = 0; i < parts.length && i < maxItems; i++) {
            if (i > 0) {
                limited.append(SEP);
            }
            limited.append(parts[i]);
        }
        prefs.edit().putString(key, limited.toString()).apply();
    }

    private static String trim(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "...";
    }

    private static boolean isBankSmsStatus(String status) {
        return "APPROVED".equals(status)
                || "APPROVED_DUPLICATE".equals(status)
                || "NO_PENDING_MATCH".equals(status)
                || "AMBIGUOUS".equals(status)
                || "FAILED".equals(status)
                || "BANK_SKIPPED".equals(status);
    }

    private static String statusTitle(String status) {
        if ("APPROVED".equals(status)) {
            return "✅ تایید شد";
        }
        if ("APPROVED_DUPLICATE".equals(status)) {
            return "✅ قبلاً تایید شده بود";
        }
        if ("NO_PENDING_MATCH".equals(status)) {
            return "❌ تایید نشد؛ پرداخت در انتظار پیدا نشد";
        }
        if ("AMBIGUOUS".equals(status)) {
            return "❌ چند پرداخت مشابه پیدا شد؛ نیاز به بررسی ادمین";
        }
        if ("FAILED".equals(status)) {
            return "🔴 خطا در ارسال یا ارتباط";
        }
        if ("BANK_SKIPPED".equals(status)) {
            return "❌ پیامک بانکی تایید نشد";
        }
        if ("SKIPPED".equals(status)) {
            return "⚪ نادیده گرفته شد";
        }
        if ("TEST_SENT".equals(status)) {
            return "🧪 تست اتصال موفق";
        }
        if ("TEST_FAILED".equals(status)) {
            return "🔴 تست اتصال ناموفق";
        }
        if ("INBOX_SCAN_START".equals(status)) {
            return "🔎 شروع بررسی پیامک‌های قبلی";
        }
        if ("INBOX_SCAN_DONE".equals(status)) {
            return "✅ بررسی پیامک‌های قبلی تمام شد";
        }
        if ("INBOX_SCAN_FAILED".equals(status)) {
            return "🔴 بررسی پیامک‌های قبلی ناموفق بود";
        }
        return "ℹ️ " + (status == null ? "گزارش" : status);
    }
}
