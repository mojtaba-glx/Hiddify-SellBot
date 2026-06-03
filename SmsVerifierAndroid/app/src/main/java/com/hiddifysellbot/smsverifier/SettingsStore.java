package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class SettingsStore {
    private static final Pattern URL_PATTERN = Pattern.compile("(https?://[^\\s<>\"']+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern HEX_SECRET_PATTERN = Pattern.compile("(?i)([a-f0-9]{32,128})");

    private static final String PREF_NAME = "sellbot_sms_verifier";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_WEBHOOK_URL = "webhook_url";
    private static final String KEY_SECRET = "secret";
    private static final String KEY_SENDER_FILTERS = "sender_filters";
    private static final String KEY_CARD_LAST4_ENABLED = "card_last4_enabled";
    private static final String KEY_THEME_MODE = "theme_mode";
    private static final String KEY_CUSTOM_BANK_COUNT = "custom_bank_count";
    private static final int MAX_CUSTOM_BANKS = 5;
    public static final String THEME_SYSTEM = "system";
    public static final String THEME_LIGHT = "light";
    public static final String THEME_DARK = "dark";
    private static final String[] BANK_IDS = {
            "blu",
            "middle_east",
            "melli",
            "mellat",
            "keshavarzi",
            "pasargad"
    };
    private static final String[] BANK_NAMES = {
            "بلو بانک",
            "بانک خاورمیانه",
            "بانک ملی",
            "بانک ملت",
            "بانک کشاورزی",
            "بانک پاسارگاد"
    };

    private final SharedPreferences prefs;

    public SettingsStore(Context context) {
        prefs = context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
    }

    public boolean isEnabled() {
        return prefs.getBoolean(KEY_ENABLED, false);
    }

    public String getWebhookUrl() {
        return normalizeWebhookUrl(prefs.getString(KEY_WEBHOOK_URL, ""));
    }

    public String getSecret() {
        return normalizeSecret(prefs.getString(KEY_SECRET, ""));
    }

    public String getSenderFiltersRaw() {
        return prefs.getString(KEY_SENDER_FILTERS, "");
    }

    public boolean isCardLast4Enabled() {
        return prefs.getBoolean(KEY_CARD_LAST4_ENABLED, false);
    }

    public String getThemeMode() {
        String mode = prefs.getString(KEY_THEME_MODE, THEME_SYSTEM);
        if (THEME_LIGHT.equals(mode) || THEME_DARK.equals(mode) || THEME_SYSTEM.equals(mode)) {
            return mode;
        }
        return THEME_SYSTEM;
    }

    public void save(
            boolean enabled,
            String webhookUrl,
            String secret,
            String senderFilters,
            boolean cardLast4Enabled
    ) {
        prefs.edit()
                .putBoolean(KEY_ENABLED, enabled)
                .putString(KEY_WEBHOOK_URL, normalizeWebhookUrl(webhookUrl))
                .putString(KEY_SECRET, normalizeSecret(secret))
                .putString(KEY_SENDER_FILTERS, safe(senderFilters))
                .putBoolean(KEY_CARD_LAST4_ENABLED, cardLast4Enabled)
                .apply();
    }

    public void saveThemeMode(String mode) {
        String selected = THEME_LIGHT.equals(mode) || THEME_DARK.equals(mode) ? mode : THEME_SYSTEM;
        prefs.edit().putString(KEY_THEME_MODE, selected).apply();
    }

    public int getBankCount() {
        return BANK_IDS.length + getCustomBankCount();
    }

    public String getBankName(int index) {
        BankConfig bank = getBank(index);
        return bank.name;
    }

    public String[] getBankNames() {
        String[] out = new String[getBankCount()];
        for (int i = 0; i < out.length; i++) {
            out[i] = getBankName(i);
        }
        return out;
    }

    public int getCustomBankCount() {
        return Math.max(0, prefs.getInt(KEY_CUSTOM_BANK_COUNT, 0));
    }

    public boolean isCustomBankIndex(int index) {
        return index >= BANK_IDS.length && index < getBankCount();
    }

    public int addCustomBank(String name, boolean enabled, String senderFilters, String sampleSms) {
        int count = getCustomBankCount();
        if (count >= MAX_CUSTOM_BANKS) {
            return -1;
        }
        String prefix = customBankPrefix(count);
        prefs.edit()
                .putInt(KEY_CUSTOM_BANK_COUNT, count + 1)
                .putString(prefix + "_name", safe(name).isEmpty() ? "بانک جدید" : safe(name))
                .putBoolean(prefix + "_enabled", enabled)
                .putString(prefix + "_senders", safe(senderFilters))
                .putString(prefix + "_sample", safe(sampleSms))
                .apply();
        return BANK_IDS.length + count;
    }

    public String getCustomBankNameHint() {
        return "مثال: سامان، رسالت، تجارت";
    }

    public String getBankTitleForEdit(int index) {
        BankConfig bank = getBank(index);
        if (bank.name.isEmpty()) {
            return "";
        }
        return bank.custom ? "بانک سفارشی: " + bank.name : "بانک آماده: " + bank.name;
    }

    public BankConfig getBank(int index) {
        if (index < 0 || index >= getBankCount()) {
            return new BankConfig("", "", false, "", "", false);
        }
        if (index >= BANK_IDS.length) {
            int customIndex = index - BANK_IDS.length;
            String prefix = customBankPrefix(customIndex);
            return new BankConfig(
                    "custom_" + customIndex,
                    prefs.getString(prefix + "_name", "بانک سفارشی " + (customIndex + 1)),
                    prefs.getBoolean(prefix + "_enabled", false),
                    prefs.getString(prefix + "_senders", ""),
                    prefs.getString(prefix + "_sample", ""),
                    true
            );
        }
        String prefix = bankPrefix(BANK_IDS[index]);
        return new BankConfig(
                BANK_IDS[index],
                BANK_NAMES[index],
                prefs.getBoolean(prefix + "_enabled", false),
                prefs.getString(prefix + "_senders", ""),
                prefs.getString(prefix + "_sample", ""),
                false
        );
    }

    public void saveBank(int index, boolean enabled, String senderFilters, String sampleSms) {
        saveBank(index, getBank(index).name, enabled, senderFilters, sampleSms);
    }

    public void saveBank(int index, String name, boolean enabled, String senderFilters, String sampleSms) {
        if (index < 0 || index >= BANK_IDS.length) {
            if (!isCustomBankIndex(index)) {
                return;
            }
            int customIndex = index - BANK_IDS.length;
            String prefix = customBankPrefix(customIndex);
            prefs.edit()
                    .putString(prefix + "_name", safe(name).isEmpty() ? "بانک سفارشی " + (customIndex + 1) : safe(name))
                    .putBoolean(prefix + "_enabled", enabled)
                    .putString(prefix + "_senders", safe(senderFilters))
                    .putString(prefix + "_sample", safe(sampleSms))
                    .apply();
            return;
        }
        String prefix = bankPrefix(BANK_IDS[index]);
        prefs.edit()
                .putBoolean(prefix + "_enabled", enabled)
                .putString(prefix + "_senders", safe(senderFilters))
                .putString(prefix + "_sample", safe(sampleSms))
                .apply();
    }

    public String getBanksSummary() {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (!bank.enabled) {
                continue;
            }
            if (out.length() > 0) {
                out.append("\n");
            }
            out.append("✅ ")
                    .append(bank.name)
                    .append(" | سرشماره: ")
                    .append(bank.senderFilters.trim().isEmpty() ? "ثبت نشده" : bank.senderFilters.trim().replace("\n", "، "));
            if (bank.sampleSms.trim().isEmpty()) {
                out.append(" | نمونه SMS: ثبت نشده");
            }
        }
        if (out.length() == 0) {
            return "هنوز بانکی فعال نشده است.";
        }
        return out.toString();
    }

    public boolean hasSenderFilters() {
        return !getSenderFilters().isEmpty();
    }

    public boolean matchesSender(String sender) {
        return !getMatchedBankName(sender).isEmpty();
    }

    public String getMatchedBankName(String sender) {
        String normalizedSender = safe(sender).toLowerCase();
        if (normalizedSender.isEmpty()) {
            return "";
        }

        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (!bank.enabled) {
                continue;
            }
            if (matchesAnyFilter(normalizedSender, parseFilters(bank.senderFilters))) {
                return bank.name;
            }
        }

        if (matchesAnyFilter(normalizedSender, parseFilters(getSenderFiltersRaw()))) {
            return "تنظیمات عمومی";
        }
        return "";
    }

    public List<String> getSenderFilters() {
        List<String> out = new ArrayList<>();
        out.addAll(parseFilters(getSenderFiltersRaw()));
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (bank.enabled) {
                out.addAll(parseFilters(bank.senderFilters));
            }
        }
        return out;
    }

    public boolean canSendWebhook() {
        return isEnabled()
                && !safe(getWebhookUrl()).isEmpty()
                && !safe(getSecret()).isEmpty()
                && hasSenderFilters();
    }

    private static boolean matchesAnyFilter(String normalizedSender, List<String> filters) {
        if (filters.isEmpty()) {
            return false;
        }
        for (String filter : filters) {
            if ("*".equals(filter) || "all".equals(filter) || "همه".equals(filter)) {
                return true;
            }
            if (normalizedSender.contains(filter) || filter.contains(normalizedSender)) {
                return true;
            }
        }
        return false;
    }

    private static List<String> parseFilters(String rawValue) {
        String raw = safe(rawValue).toLowerCase();
        String[] parts = raw.split("[,;\\n\\r]+");
        List<String> out = new ArrayList<>();
        for (String part : parts) {
            String item = part == null ? "" : part.trim();
            if (!item.isEmpty()) {
                out.add(item);
            }
        }
        return out;
    }

    private static String bankPrefix(String id) {
        return "bank_" + id;
    }

    private static String customBankPrefix(int index) {
        return "custom_bank_" + index;
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    public static String normalizeWebhookUrl(String value) {
        String text = safe(value);
        Matcher matcher = URL_PATTERN.matcher(text);
        if (matcher.find()) {
            return trimUrlTail(matcher.group(1));
        }
        return trimUrlTail(text);
    }

    public static String normalizeSecret(String value) {
        String text = safe(value);
        Matcher matcher = HEX_SECRET_PATTERN.matcher(text);
        String best = "";
        while (matcher.find()) {
            String candidate = matcher.group(1);
            if (candidate.length() > best.length()) {
                best = candidate;
            }
        }
        if (!best.isEmpty()) {
            return best;
        }
        return text.replaceAll("\\s+", "");
    }

    private static String trimUrlTail(String value) {
        String text = safe(value);
        while (text.endsWith(".") || text.endsWith(",") || text.endsWith(")") || text.endsWith("]")) {
            text = text.substring(0, text.length() - 1).trim();
        }
        return text;
    }

    public static final class BankConfig {
        public final String id;
        public final String name;
        public final boolean enabled;
        public final String senderFilters;
        public final String sampleSms;
        public final boolean custom;

        private BankConfig(String id, String name, boolean enabled, String senderFilters, String sampleSms, boolean custom) {
            this.id = id;
            this.name = name;
            this.enabled = enabled;
            this.senderFilters = senderFilters == null ? "" : senderFilters;
            this.sampleSms = sampleSms == null ? "" : sampleSms;
            this.custom = custom;
        }
    }
}
