package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Set;
import java.util.Locale;

public final class HistoryStore {
    private static final String PREF_NAME = "sellbot_sms_history";
    private static final String KEY_HISTORY = "history";
    private static final String KEY_APPROVED_HISTORY = "approved_history";
    private static final String KEY_BANK_SMS_HISTORY = "bank_sms_history";
    private static final String KEY_MANUAL_APPROVED_IDS = "manual_approved_ids";
    private static final String SEP = "\n---\n";
    private static final int MAX_ITEMS = 80;
    private static final int MAX_APPROVED_ITEMS = 40;
    private static final int MAX_BANK_SMS_ITEMS = 80;

    private HistoryStore() {
    }

    public static void add(Context context, String status, String detail) {
        String entry = buildEntry(status, detail);
        if (!isBankSmsStatus(status)) {
            save(context, KEY_HISTORY, entry, MAX_ITEMS);
        }
        if ("APPROVED".equals(status) || "APPROVED_DUPLICATE".equals(status) || "MANUAL_APPROVED".equals(status)) {
            save(context, KEY_APPROVED_HISTORY, entry, MAX_APPROVED_ITEMS);
        }
        if (isBankSmsStatus(status)) {
            save(context, KEY_BANK_SMS_HISTORY, entry, MAX_BANK_SMS_ITEMS);
        }
    }

    public static void addUnique(Context context, String status, String detail, String uniqueId) {
        String marker = uniqueMarker(uniqueId);
        if (!marker.isEmpty()) {
            String bankHistory = getBankSms(context);
            String generalHistory = get(context);
            if ((bankHistory != null && bankHistory.contains(marker))
                    || (generalHistory != null && generalHistory.contains(marker))) {
                return;
            }
        }
        add(context, status, detail);
    }

    public static void upsertUnique(Context context, String status, String detail, String uniqueId) {
        String marker = uniqueMarker(uniqueId);
        if (!marker.isEmpty()) {
            SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
            prefs.edit()
                    .putString(KEY_HISTORY, removeEntriesContaining(prefs.getString(KEY_HISTORY, ""), marker))
                    .putString(KEY_APPROVED_HISTORY, removeEntriesContaining(prefs.getString(KEY_APPROVED_HISTORY, ""), marker))
                    .putString(KEY_BANK_SMS_HISTORY, removeEntriesContaining(prefs.getString(KEY_BANK_SMS_HISTORY, ""), marker))
                    .apply();
        }
        add(context, status, detail);
    }

    public static void upsertUniqueSms(Context context, String status, String detail, String uniqueId, String sender, String body) {
        String marker = uniqueMarker(uniqueId);
        String senderKey = normalizeSender(sender);
        String bodyKey = normalizeSmsBody(body);
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        prefs.edit()
                .putString(KEY_HISTORY, removeEntriesMatchingSms(prefs.getString(KEY_HISTORY, ""), marker, senderKey, bodyKey))
                .putString(KEY_APPROVED_HISTORY, removeEntriesMatchingSms(prefs.getString(KEY_APPROVED_HISTORY, ""), marker, senderKey, bodyKey))
                .putString(KEY_BANK_SMS_HISTORY, removeEntriesMatchingSms(prefs.getString(KEY_BANK_SMS_HISTORY, ""), marker, senderKey, bodyKey))
                .apply();
        add(context, status, detail);
    }

    public static boolean containsUnique(Context context, String uniqueId) {
        String marker = uniqueMarker(uniqueId);
        if (marker.isEmpty()) {
            return false;
        }
        String bankHistory = getBankSms(context);
        String generalHistory = get(context);
        String approvedHistory = getApproved(context);
        return (bankHistory != null && bankHistory.contains(marker))
                || (generalHistory != null && generalHistory.contains(marker))
                || (approvedHistory != null && approvedHistory.contains(marker));
    }

    public static boolean shouldRetryUnique(Context context, String uniqueId) {
        String marker = uniqueMarker(uniqueId);
        if (marker.isEmpty()) {
            return true;
        }
        String raw = findEntryContaining(getBankSms(context), marker);
        if (raw.isEmpty()) {
            raw = findEntryContaining(get(context), marker);
        }
        if (raw.isEmpty()) {
            raw = findEntryContaining(getApproved(context), marker);
        }
        if (raw.isEmpty()) {
            return true;
        }
        String text = raw.toLowerCase(Locale.US);
        return text.contains("no_pending_match")
                || raw.contains("پرداخت pending پیدا نشد")
                || raw.contains("پرداخت در انتظار پیدا نشد")
                || raw.contains("پرداخت در انتظار با این مبلغ پیدا نشد")
                || raw.contains("📨 پیامک به ربات ارسال شد")
                || raw.contains("📨 به ربات ارسال شد")
                || text.contains("retry\":true");
    }

    public static boolean markBankSmsManuallyApproved(Context context, String uniqueId) {
        String marker = uniqueMarker(uniqueId);
        String value = uniqueId == null ? "" : uniqueId.trim();
        if (value.isEmpty()) {
            return false;
        }
        if (!marker.isEmpty()) {
            String raw = findEntryContaining(getBankSms(context), marker);
            if (raw.isEmpty()) {
                raw = findEntryContaining(get(context), marker);
            }
            if (raw.isEmpty()) {
                raw = findEntryContaining(getApproved(context), marker);
            }
        }
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        Set<String> ids = new java.util.HashSet<>(prefs.getStringSet(KEY_MANUAL_APPROVED_IDS, new java.util.HashSet<String>()));
        ids.add(value);
        prefs.edit().putStringSet(KEY_MANUAL_APPROVED_IDS, ids).apply();
        return true;
    }

    public static boolean isManuallyApproved(Context context, String uniqueId) {
        String value = uniqueId == null ? "" : uniqueId.trim();
        if (value.isEmpty()) {
            return false;
        }
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        return prefs.getStringSet(KEY_MANUAL_APPROVED_IDS, new java.util.HashSet<String>()).contains(value);
    }

    public static void syncBankSmsWithInbox(Context context, Set<String> currentInboxEventIds) {
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        prefs.edit()
                .putString(KEY_BANK_SMS_HISTORY, keepEntriesInCurrentInbox(prefs.getString(KEY_BANK_SMS_HISTORY, ""), currentInboxEventIds))
                .putString(KEY_APPROVED_HISTORY, keepEntriesInCurrentInbox(prefs.getString(KEY_APPROVED_HISTORY, ""), currentInboxEventIds))
                .putStringSet(KEY_MANUAL_APPROVED_IDS, keepManualApprovedIdsInCurrentInbox(prefs.getStringSet(KEY_MANUAL_APPROVED_IDS, new java.util.HashSet<String>()), currentInboxEventIds))
                .apply();
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

    public static Entry[] getBankSmsEntries(Context context) {
        return parseEntries(getBankSms(context));
    }

    public static void clear(Context context) {
        context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_HISTORY)
                .remove(KEY_APPROVED_HISTORY)
                .remove(KEY_BANK_SMS_HISTORY)
                .remove(KEY_MANUAL_APPROVED_IDS)
                .apply();
    }

    public static void clearTechnicalLogs(Context context) {
        context.getApplicationContext()
                .getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_HISTORY)
                .apply();
    }

    private static String buildEntry(String status, String detail) {
        String now = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date());
        return "🕒 " + now + "\n" + statusTitle(status) + "\n" + trim(detail, 1800);
    }

    private static String uniqueMarker(String uniqueId) {
        String value = uniqueId == null ? "" : uniqueId.trim();
        return value.isEmpty() ? "" : "🔐 شناسه داخلی: " + value;
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

    private static String removeEntriesContaining(String raw, String marker) {
        if (raw == null || raw.trim().isEmpty() || marker == null || marker.trim().isEmpty()) {
            return raw == null ? "" : raw;
        }
        String[] parts = raw.split(SEP);
        StringBuilder out = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.contains(marker)) {
                continue;
            }
            if (out.length() > 0) {
                out.append(SEP);
            }
            out.append(part);
        }
        return out.toString();
    }

    private static String removeEntriesMatchingSms(String raw, String marker, String senderKey, String bodyKey) {
        if (raw == null || raw.trim().isEmpty()) {
            return "";
        }
        String[] parts = raw.split(SEP);
        StringBuilder out = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.trim().isEmpty()) {
                continue;
            }
            if (marker != null && !marker.trim().isEmpty() && part.contains(marker)) {
                continue;
            }
            String oldBody = normalizeSmsBody(extractSmsBody(part));
            String oldSender = normalizeSender(extractLine(part, "👤 سرشماره:"));
            boolean sameBody = !bodyKey.isEmpty() && bodyKey.equals(oldBody);
            boolean sameSender = senderKey.isEmpty() || oldSender.isEmpty() || senderKey.equals(oldSender);
            if (sameBody && sameSender) {
                continue;
            }
            appendPart(out, part);
        }
        return out.toString();
    }

    private static String findEntryContaining(String raw, String marker) {
        if (raw == null || raw.trim().isEmpty() || marker == null || marker.trim().isEmpty()) {
            return "";
        }
        String[] parts = raw.split(SEP);
        for (String part : parts) {
            if (part != null && part.contains(marker)) {
                return part;
            }
        }
        return "";
    }

    private static String keepEntriesInCurrentInbox(String raw, Set<String> currentInboxEventIds) {
        if (raw == null || raw.trim().isEmpty()) {
            return "";
        }
        Set<String> allowed = currentInboxEventIds;
        String[] parts = raw.split(SEP);
        StringBuilder out = new StringBuilder();
        for (String part : parts) {
            if (part == null || part.trim().isEmpty()) {
                continue;
            }
            String eventId = extractUniqueId(part);
            if (eventId.isEmpty()) {
                if (isInboxScanRaw(part)) {
                    continue;
                }
                appendPart(out, part);
                continue;
            }
            if (allowed != null && allowed.contains(eventId)) {
                appendPart(out, part);
            }
        }
        return out.toString();
    }

    private static Set<String> keepManualApprovedIdsInCurrentInbox(Set<String> rawIds, Set<String> currentInboxEventIds) {
        Set<String> out = new java.util.HashSet<>();
        if (rawIds == null || rawIds.isEmpty() || currentInboxEventIds == null || currentInboxEventIds.isEmpty()) {
            return out;
        }
        for (String id : rawIds) {
            String value = id == null ? "" : id.trim();
            if (!value.isEmpty() && currentInboxEventIds.contains(value)) {
                out.add(value);
            }
        }
        return out;
    }

    private static void appendPart(StringBuilder out, String part) {
        if (out.length() > 0) {
            out.append(SEP);
        }
        out.append(part);
    }

    private static boolean isInboxScanRaw(String raw) {
        String text = raw == null ? "" : raw;
        return text.contains("بررسی پیامک‌های قبلی")
                || text.contains("INBOX_SCAN")
                || text.contains("پیامک‌های بررسی‌شده");
    }

    private static String extractUniqueId(String raw) {
        if (raw == null || raw.trim().isEmpty()) {
            return "";
        }
        String[] lines = raw.split("\\n");
        for (String line : lines) {
            String text = line == null ? "" : line.trim();
            if (text.startsWith("🔐 شناسه داخلی:")) {
                return text.substring("🔐 شناسه داخلی:".length()).trim();
            }
        }
        return "";
    }

    private static String extractLine(String raw, String prefix) {
        if (raw == null || prefix == null || prefix.trim().isEmpty()) {
            return "";
        }
        String[] lines = raw.split("\\n");
        for (String line : lines) {
            String text = line == null ? "" : line.trim();
            if (text.startsWith(prefix)) {
                return text.substring(prefix.length()).trim();
            }
        }
        return "";
    }

    private static String extractSmsBody(String raw) {
        if (raw == null || raw.trim().isEmpty()) {
            return "";
        }
        String marker = "📄 متن SMS:";
        int index = raw.indexOf(marker);
        if (index < 0) {
            marker = "متن SMS:";
            index = raw.indexOf(marker);
        }
        if (index < 0) {
            return "";
        }
        return raw.substring(index + marker.length()).trim();
    }

    private static String normalizeSmsBody(String body) {
        return PaymentSmsParser.normalizeDigits(body == null ? "" : body)
                .replace("\r", "\n")
                .replaceAll("\\s+", " ")
                .trim();
    }

    private static String normalizeSender(String sender) {
        String digits = PaymentSmsParser.normalizeDigits(sender == null ? "" : sender).replaceAll("[^0-9]", "");
        if (digits.startsWith("0098") && digits.length() > 6) {
            digits = "0" + digits.substring(4);
        } else if (digits.startsWith("98") && digits.length() > 10) {
            digits = "0" + digits.substring(2);
        }
        return digits;
    }

    private static Entry[] parseEntries(String raw) {
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

    private static String trim(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "...";
    }

    private static boolean isBankSmsStatus(String status) {
        return "APPROVED".equals(status)
                || "MANUAL_APPROVED".equals(status)
                || "APPROVED_DUPLICATE".equals(status)
                || "NO_PENDING_MATCH".equals(status)
                || "SMS_REUSED".equals(status)
                || "AMBIGUOUS".equals(status)
                || "SENT".equals(status)
                || "FAILED".equals(status)
                || "BANK_SKIPPED".equals(status);
    }

    private static boolean isInboxScanStatus(String status) {
        return "INBOX_SCAN_START".equals(status)
                || "INBOX_SCAN_DONE".equals(status)
                || "INBOX_SCAN_FAILED".equals(status);
    }

    private static String statusTitle(String status) {
        if ("APPROVED".equals(status)) {
            return "✅ تایید شد";
        }
        if ("MANUAL_APPROVED".equals(status)) {
            return "✅ تایید دستی داخل اپ";
        }
        if ("APPROVED_DUPLICATE".equals(status)) {
            return "✅ قبلاً تایید شده بود؛ درآمد جدید نیست";
        }
        if ("NO_PENDING_MATCH".equals(status)) {
            return "🟡 تایید نشد؛ پرداخت در انتظار پیدا نشد";
        }
        if ("SMS_REUSED".equals(status)) {
            return "🟡 این SMS قبلاً برای پرداخت دیگری استفاده شده است";
        }
        if ("AMBIGUOUS".equals(status)) {
            return "❌ چند پرداخت مشابه پیدا شد؛ نیاز به بررسی ادمین";
        }
        if ("SENT".equals(status)) {
            return "📨 پیامک به ربات ارسال شد";
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

    public static final class Entry {
        public final String raw;
        public final String time;
        public final String title;
        public final String detail;
        public final boolean approved;
        public final boolean rejected;

        private Entry(String raw, String time, String title, String detail, boolean approved, boolean rejected) {
            this.raw = raw;
            this.time = time;
            this.title = title;
            this.detail = detail;
            this.approved = approved;
            this.rejected = rejected;
        }

        private static Entry fromRaw(String rawValue) {
            String raw = rawValue == null ? "" : rawValue.trim();
            String[] lines = raw.split("\\n", 3);
            String time = lines.length > 0 ? lines[0].replace("🕒", "").trim() : "";
            String title = lines.length > 1 ? lines[1].trim() : "گزارش";
            String detail = lines.length > 2 ? lines[2].trim() : "";
            boolean approved = title.contains("✅") && !title.contains("قبلاً");
            boolean rejected = title.contains("❌") || title.contains("🔴");
            return new Entry(raw, time, title, detail, approved, rejected);
        }
    }
}
