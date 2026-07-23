package com.hiddifysellbot.smsverifier;

public final class SmsEvent {
    public String eventId;
    public String sender;
    public String body;
    public long amount;
    public String currency;
    public String reference;
    public String cardLast4;
    public boolean cardLast4Required;
    public boolean test;
    public long receivedAt;
    public long deviceTime;

    public String toJson() {
        return "{"
                + "\"event_id\":\"" + json(eventId) + "\","
                + "\"source\":\"android_sms\","
                + "\"sender\":\"" + json(sender) + "\","
                + "\"body\":\"" + json(body) + "\","
                + "\"amount\":" + amount + ","
                + "\"currency\":\"" + json(currency) + "\","
                + "\"reference\":\"" + json(reference) + "\","
                + "\"card_last4\":\"" + json(cardLast4) + "\","
                + "\"card_last4_required\":" + cardLast4Required + ","
                + "\"test\":" + test + ","
                + "\"received_at\":" + receivedAt + ","
                + "\"device_time\":" + deviceTime
                + "}";
    }

    private static String json(String value) {
        if (value == null) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 32) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}
