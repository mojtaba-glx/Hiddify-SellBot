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
    private static final String KEY_CARD_LAST4 = "card_last4";

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

    public String getCardLast4() {
        return PaymentSmsParser.normalizeDigits(prefs.getString(KEY_CARD_LAST4, "")).replaceAll("[^0-9]", "");
    }

    public void save(
            boolean enabled,
            String webhookUrl,
            String secret,
            String senderFilters,
            boolean cardLast4Enabled,
            String cardLast4
    ) {
        prefs.edit()
                .putBoolean(KEY_ENABLED, enabled)
                .putString(KEY_WEBHOOK_URL, normalizeWebhookUrl(webhookUrl))
                .putString(KEY_SECRET, normalizeSecret(secret))
                .putString(KEY_SENDER_FILTERS, safe(senderFilters))
                .putBoolean(KEY_CARD_LAST4_ENABLED, cardLast4Enabled)
                .putString(KEY_CARD_LAST4, PaymentSmsParser.normalizeDigits(safe(cardLast4)).replaceAll("[^0-9]", ""))
                .apply();
    }

    public boolean hasSenderFilters() {
        return !getSenderFilters().isEmpty();
    }

    public boolean matchesSender(String sender) {
        String normalizedSender = safe(sender).toLowerCase();
        List<String> filters = getSenderFilters();
        if (normalizedSender.isEmpty() || filters.isEmpty()) {
            return false;
        }
        for (String filter : filters) {
            if (normalizedSender.contains(filter) || filter.contains(normalizedSender)) {
                return true;
            }
        }
        return false;
    }

    public List<String> getSenderFilters() {
        String raw = getSenderFiltersRaw().toLowerCase();
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

    public boolean canSendWebhook() {
        return isEnabled()
                && !safe(getWebhookUrl()).isEmpty()
                && !safe(getSecret()).isEmpty()
                && hasSenderFilters();
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
}
