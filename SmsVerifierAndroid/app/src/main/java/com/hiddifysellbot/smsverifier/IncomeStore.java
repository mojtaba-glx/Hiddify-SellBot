package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

public final class IncomeStore {
    static final String PREF_NAME = "sellbot_sms_income";
    private static final String KEY_LEDGER = "income_ledger";
    private static final String SEP = "\n---\n";
    private static final int MAX_LEDGER_ITEMS = 3000;

    private IncomeStore() {
    }

    public static boolean record(
            Context context,
            String eventId,
            long amountToman,
            String source,
            String bank,
            String sender,
            String reference,
            long timestampMs
    ) {
        String id = safe(eventId);
        if (id.isEmpty() || amountToman <= 0) {
            return false;
        }
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String old = prefs.getString(KEY_LEDGER, "");
        String marker = marker(id);
        if (old != null && old.contains(marker)) {
            return false;
        }

        long ts = timestampMs > 0 ? timestampMs : System.currentTimeMillis();
        String time = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date(ts));
        String entry = "🕒 " + time
                + "\n✅ درآمد تاییدشده"
                + "\n💰 مبلغ تومان: " + amountToman
                + "\n🏦 بانک: " + emptyDash(bank)
                + "\n👤 سرشماره: " + emptyDash(sender)
                + "\n🔖 پیگیری: " + emptyDash(reference)
                + "\n🧾 منبع: " + emptyDash(source)
                + "\n" + marker;

        String merged = old == null || old.trim().isEmpty() ? entry : entry + SEP + old;
        String[] parts = merged.split(SEP);
        StringBuilder limited = new StringBuilder();
        for (int i = 0; i < parts.length && i < MAX_LEDGER_ITEMS; i++) {
            if (i > 0) {
                limited.append(SEP);
            }
            limited.append(parts[i]);
        }
        prefs.edit().putString(KEY_LEDGER, limited.toString()).apply();
        return true;
    }

    public static Entry[] getEntries(Context context) {
        String raw = context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .getString(KEY_LEDGER, "");
        if (raw == null || raw.trim().isEmpty()) {
            return new Entry[0];
        }
        String[] parts = raw.split(SEP);
        Entry[] entries = new Entry[parts.length];
        for (int i = 0; i < parts.length; i++) {
            entries[i] = Entry.fromRaw(parts[i]);
        }
        return entries;
    }

    public static Stats getStats(Context context) {
        Calendar now = Calendar.getInstance();
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(now.getTime());
        String month = new SimpleDateFormat("yyyy-MM", Locale.US).format(now.getTime());
        Stats stats = new Stats();
        for (Entry entry : getEntries(context)) {
            if (entry.amountToman <= 0) {
                continue;
            }
            stats.approvedCount++;
            if (entry.time.startsWith(today)) {
                stats.todayTotal += entry.amountToman;
            }
            if (entry.time.startsWith(month)) {
                stats.monthTotal += entry.amountToman;
            }
            if (isInLastDays(entry.time, 7)) {
                stats.weekTotal += entry.amountToman;
            }
        }
        return stats;
    }

    private static boolean isInLastDays(String time, int days) {
        if (time == null || time.trim().length() < 10) {
            return false;
        }
        try {
            Date date = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(time.trim());
            if (date == null) {
                return false;
            }
            Calendar from = Calendar.getInstance();
            from.add(Calendar.DAY_OF_YEAR, -Math.max(1, days) + 1);
            from.set(Calendar.HOUR_OF_DAY, 0);
            from.set(Calendar.MINUTE, 0);
            from.set(Calendar.SECOND, 0);
            from.set(Calendar.MILLISECOND, 0);
            return !date.before(from.getTime());
        } catch (Exception ignored) {
            return false;
        }
    }

    private static String marker(String eventId) {
        return "🔐 شناسه داخلی: " + safe(eventId);
    }

    private static String lineValue(String detail, String prefix) {
        if (detail == null || prefix == null) {
            return "";
        }
        String[] lines = detail.split("\\n");
        for (String line : lines) {
            String value = line == null ? "" : line.trim();
            if (value.startsWith(prefix)) {
                return value.substring(prefix.length()).trim();
            }
        }
        return "";
    }

    private static long parseLong(String value) {
        try {
            String digits = PaymentSmsParser.normalizeDigits(value == null ? "" : value).replaceAll("[^0-9]", "");
            return digits.isEmpty() ? 0 : Long.parseLong(digits);
        } catch (Exception ignored) {
            return 0;
        }
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private static String emptyDash(String value) {
        String safe = safe(value);
        return safe.isEmpty() ? "-" : safe;
    }

    public static final class Stats {
        public long todayTotal;
        public long weekTotal;
        public long monthTotal;
        public int approvedCount;
    }

    public static final class Entry {
        public final String raw;
        public final String time;
        public final long amountToman;
        public final String eventId;

        private Entry(String raw, String time, long amountToman, String eventId) {
            this.raw = raw;
            this.time = time;
            this.amountToman = amountToman;
            this.eventId = eventId;
        }

        private static Entry fromRaw(String rawValue) {
            String raw = rawValue == null ? "" : rawValue.trim();
            String[] lines = raw.split("\\n", 3);
            String time = lines.length > 0 ? lines[0].replace("🕒", "").trim() : "";
            String detail = lines.length > 2 ? lines[2].trim() : "";
            long amount = parseLong(lineValue(detail, "💰 مبلغ تومان:"));
            String eventId = lineValue(detail, "🔐 شناسه داخلی:");
            return new Entry(raw, time, amount, eventId);
        }
    }
}
