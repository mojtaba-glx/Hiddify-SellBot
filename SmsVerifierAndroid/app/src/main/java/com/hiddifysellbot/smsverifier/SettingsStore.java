package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;

public final class SettingsStore {
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
        return prefs.getString(KEY_WEBHOOK_URL, "");
    }

    public String getSecret() {
        return prefs.getString(KEY_SECRET, "");
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
                .putString(KEY_WEBHOOK_URL, safe(webhookUrl))
                .putString(KEY_SECRET, safe(secret))
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
}
