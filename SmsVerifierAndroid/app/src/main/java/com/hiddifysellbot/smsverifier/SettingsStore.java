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
        return addCustomBank(name, enabled, senderFilters, sampleSms, false);
    }

    public int addCustomBank(String name, boolean enabled, String senderFilters, String sampleSms, boolean cardLast4Enabled) {
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
                .putBoolean(prefix + "_card_last4", cardLast4Enabled)
                .apply();
        return BANK_IDS.length + count;
    }

    public void deleteOrResetBank(int index) {
        if (index < 0 || index >= getBankCount()) {
            return;
        }
        if (!isCustomBankIndex(index)) {
            saveBank(index, getBank(index).name, false, "", "", false);
            return;
        }
        deleteCustomBank(index - BANK_IDS.length);
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
            return new BankConfig("", "", false, "", "", false, false);
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
                    prefs.getBoolean(prefix + "_card_last4", prefs.getBoolean(KEY_CARD_LAST4_ENABLED, false)),
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
                prefs.getBoolean(prefix + "_card_last4", prefs.getBoolean(KEY_CARD_LAST4_ENABLED, false)),
                false
        );
    }

    public void saveBank(int index, boolean enabled, String senderFilters, String sampleSms) {
        saveBank(index, getBank(index).name, enabled, senderFilters, sampleSms, getBank(index).cardLast4Enabled);
    }

    public void saveBank(int index, String name, boolean enabled, String senderFilters, String sampleSms) {
        saveBank(index, name, enabled, senderFilters, sampleSms, getBank(index).cardLast4Enabled);
    }

    public void saveBank(int index, String name, boolean enabled, String senderFilters, String sampleSms, boolean cardLast4Enabled) {
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
                    .putBoolean(prefix + "_card_last4", cardLast4Enabled)
                    .apply();
            return;
        }
        String prefix = bankPrefix(BANK_IDS[index]);
        prefs.edit()
                .putBoolean(prefix + "_enabled", enabled)
                .putString(prefix + "_senders", safe(senderFilters))
                .putString(prefix + "_sample", safe(sampleSms))
                .putBoolean(prefix + "_card_last4", cardLast4Enabled)
                .apply();
    }

    private void deleteCustomBank(int customIndex) {
        int count = getCustomBankCount();
        if (customIndex < 0 || customIndex >= count) {
            return;
        }
        SharedPreferences.Editor editor = prefs.edit();
        for (int i = customIndex; i < count - 1; i++) {
            String current = customBankPrefix(i);
            String next = customBankPrefix(i + 1);
            editor.putString(current + "_name", prefs.getString(next + "_name", "بانک سفارشی " + (i + 1)));
            editor.putBoolean(current + "_enabled", prefs.getBoolean(next + "_enabled", false));
            editor.putString(current + "_senders", prefs.getString(next + "_senders", ""));
            editor.putString(current + "_sample", prefs.getString(next + "_sample", ""));
            editor.putBoolean(current + "_card_last4", prefs.getBoolean(next + "_card_last4", false));
        }
        String last = customBankPrefix(count - 1);
        editor.remove(last + "_name")
                .remove(last + "_enabled")
                .remove(last + "_senders")
                .remove(last + "_sample")
                .remove(last + "_card_last4")
                .putInt(KEY_CUSTOM_BANK_COUNT, count - 1)
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
            out.append(" | چهار رقم کارت: ").append(bank.cardLast4Enabled ? "فعال" : "خاموش");
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
        return !getMatchedBankName(sender, "").isEmpty();
    }

    public String getMatchedBankName(String sender) {
        return getMatchedBankName(sender, "");
    }

    public String getMatchedBankName(String sender, String body) {
        String configured = getMatchedConfiguredBankName(sender, body);
        if (!configured.isEmpty()) {
            return configured;
        }

        String normalizedSender = safe(sender).toLowerCase();
        if (normalizedSender.isEmpty()) {
            return "";
        }

        if (!hasActiveBankSenderFilters() && matchesAnyFilter(normalizedSender, parseFilters(getSenderFiltersRaw()))) {
            return "تنظیمات عمومی";
        }
        return "";
    }

    public String getMatchedConfiguredBankName(String sender, String body) {
        String normalizedSender = safe(sender).toLowerCase();
        String normalizedBody = normalizeBankText(body);
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (!bank.enabled) {
                continue;
            }
            if (!normalizedSender.isEmpty() && matchesAnyFilter(normalizedSender, parseFilters(bank.senderFilters))) {
                return bank.name;
            }
        }

        if (!normalizedBody.isEmpty()) {
            for (int i = 0; i < getBankCount(); i++) {
                BankConfig bank = getBank(i);
                if (!bank.enabled) {
                    continue;
                }
                if (!parseFilters(bank.senderFilters).isEmpty()) {
                    continue;
                }
                String bankName = normalizeBankText(bank.name);
                String sample = normalizeBankText(bank.sampleSms);
                if ((!bankName.isEmpty() && normalizedBody.contains(bankName))
                        || ("blu".equals(bank.id) && normalizedBody.contains("بلو"))
                        || ("middle_east".equals(bank.id) && (normalizedBody.contains("خاورمیانه") || normalizedBody.contains("خاورميانه")))
                        || (!sample.isEmpty() && sameBankBySample(normalizedBody, sample))) {
                    return bank.name;
                }
            }
        }
        return "";
    }

    public boolean isActiveBankName(String bankName) {
        String needle = normalizeBankText(bankName);
        if (needle.isEmpty()) {
            return false;
        }
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (!bank.enabled) {
                continue;
            }
            String current = normalizeBankText(bank.name);
            if (!current.isEmpty() && (current.equals(needle) || needle.contains(current) || current.contains(needle))) {
                return true;
            }
        }
        return false;
    }

    public boolean isCardLast4EnabledFor(String sender, String body) {
        String bankName = getMatchedConfiguredBankName(sender, body);
        if (!bankName.isEmpty()) {
            String needle = normalizeBankText(bankName);
            for (int i = 0; i < getBankCount(); i++) {
                BankConfig bank = getBank(i);
                if (!bank.enabled) {
                    continue;
                }
                if (normalizeBankText(bank.name).equals(needle)) {
                    return bank.cardLast4Enabled;
                }
            }
        }
        return prefs.getBoolean(KEY_CARD_LAST4_ENABLED, false);
    }

    public List<String> getSenderFilters() {
        List<String> out = new ArrayList<>();
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (bank.enabled) {
                out.addAll(parseFilters(bank.senderFilters));
            }
        }
        if (out.isEmpty()) {
            out.addAll(parseFilters(getSenderFiltersRaw()));
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
        String senderDigits = normalizePhoneDigits(normalizedSender);
        for (String filter : filters) {
            if ("*".equals(filter) || "all".equals(filter) || "همه".equals(filter)) {
                return true;
            }
            String filterDigits = normalizePhoneDigits(filter);
            if (senderDigits.length() >= 4 && filterDigits.length() >= 4) {
                if (senderDigits.endsWith(filterDigits)
                        || filterDigits.endsWith(senderDigits)
                        || senderDigits.contains(filterDigits)
                        || filterDigits.contains(senderDigits)) {
                    return true;
                }
                continue;
            }
            if (filterDigits.length() >= 4 || senderDigits.length() >= 4) {
                continue;
            }
            if (filter.length() >= 3 && (normalizedSender.contains(filter) || filter.contains(normalizedSender))) {
                return true;
            }
        }
        return false;
    }

    private boolean hasActiveBankSenderFilters() {
        for (int i = 0; i < getBankCount(); i++) {
            BankConfig bank = getBank(i);
            if (bank.enabled && !parseFilters(bank.senderFilters).isEmpty()) {
                return true;
            }
        }
        return false;
    }

    private static boolean sameBankBySample(String body, String sample) {
        String sampleFirstLine = sample.split("\\n", 2)[0].trim();
        return sampleFirstLine.length() >= 2 && body.contains(sampleFirstLine);
    }

    private static String normalizeBankText(String value) {
        return safe(value)
                .toLowerCase()
                .replace("ي", "ی")
                .replace("ك", "ک")
                .replace("‌", " ")
                .replaceAll("\\s+", " ")
                .trim();
    }

    private static String normalizePhoneDigits(String value) {
        String digits = safe(value)
                .replace('۰', '0').replace('۱', '1').replace('۲', '2').replace('۳', '3').replace('۴', '4')
                .replace('۵', '5').replace('۶', '6').replace('۷', '7').replace('۸', '8').replace('۹', '9')
                .replace('٠', '0').replace('١', '1').replace('٢', '2').replace('٣', '3').replace('٤', '4')
                .replace('٥', '5').replace('٦', '6').replace('٧', '7').replace('٨', '8').replace('٩', '9')
                .replaceAll("[^0-9]", "");
        if (digits.startsWith("0098") && digits.length() > 6) {
            digits = "0" + digits.substring(4);
        } else if (digits.startsWith("98") && digits.length() > 10) {
            digits = "0" + digits.substring(2);
        } else if (digits.startsWith("9") && digits.length() == 10) {
            digits = "0" + digits;
        }
        return digits;
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
        public final boolean cardLast4Enabled;
        public final boolean custom;

        private BankConfig(String id, String name, boolean enabled, String senderFilters, String sampleSms, boolean cardLast4Enabled, boolean custom) {
            this.id = id;
            this.name = name;
            this.enabled = enabled;
            this.senderFilters = senderFilters == null ? "" : senderFilters;
            this.sampleSms = sampleSms == null ? "" : sampleSms;
            this.cardLast4Enabled = cardLast4Enabled;
            this.custom = custom;
        }
    }
}
