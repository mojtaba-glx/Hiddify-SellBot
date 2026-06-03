package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public final class HistoryStore {
    private static final String PREF_NAME = "sellbot_sms_history";
    private static final String KEY_HISTORY = "history";
    private static final String SEP = "\n---\n";
    private static final int MAX_ITEMS = 80;

    private HistoryStore() {
    }

    public static void add(Context context, String status, String detail) {
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String now = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date());
        String entry = now + " | " + status + "\n" + trim(detail, 700);
        String old = prefs.getString(KEY_HISTORY, "");
        String merged = old == null || old.isEmpty() ? entry : entry + SEP + old;
        String[] parts = merged.split(SEP);
        StringBuilder limited = new StringBuilder();
        for (int i = 0; i < parts.length && i < MAX_ITEMS; i++) {
            if (i > 0) {
                limited.append(SEP);
            }
            limited.append(parts[i]);
        }
        prefs.edit().putString(KEY_HISTORY, limited.toString()).apply();
    }

    public static String get(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_HISTORY, "");
    }

    public static void clear(Context context) {
        context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_HISTORY)
                .apply();
    }

    private static String trim(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "...";
    }
}
