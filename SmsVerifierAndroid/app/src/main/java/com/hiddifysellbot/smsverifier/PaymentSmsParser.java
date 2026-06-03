package com.hiddifysellbot.smsverifier;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class PaymentSmsParser {
    private static final Pattern KEYWORD_AMOUNT = Pattern.compile(
            "(?i)(?:مبلغ|واریز|واريز|برداشت|انتقال|پرداخت|خرید|خريد|amount|deposit|credit|debit)[^0-9۰-۹٠-٩]{0,40}([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\s]{2,24})"
    );
    private static final Pattern ANY_NUMBER = Pattern.compile("([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\s]{3,24})");
    private static final Pattern REFERENCE = Pattern.compile(
            "(?i)(?:پیگیری|رهگیری|مرجع|ارجاع|شناسه|reference|ref|trace)[^0-9۰-۹٠-٩]{0,30}([0-9۰-۹٠-٩]{4,30})"
    );

    private PaymentSmsParser() {
    }

    public static String normalizeDigits(String input) {
        if (input == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(input.length());
        for (int i = 0; i < input.length(); i++) {
            char c = input.charAt(i);
            if (c >= '۰' && c <= '۹') {
                out.append((char) ('0' + (c - '۰')));
            } else if (c >= '٠' && c <= '٩') {
                out.append((char) ('0' + (c - '٠')));
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    public static long extractAmount(String body) {
        String text = normalizeDigits(body);
        long amount = extractAmountByPattern(text, KEYWORD_AMOUNT);
        if (amount > 0) {
            return amount;
        }

        Matcher matcher = ANY_NUMBER.matcher(text);
        long best = 0;
        while (matcher.find()) {
            long candidate = parseNumber(matcher.group(1));
            if (candidate >= 1000 && candidate <= 1_000_000_000_000L && candidate > best) {
                best = candidate;
            }
        }
        return best;
    }

    public static String extractCurrency(String body) {
        String lower = normalizeDigits(body).toLowerCase(Locale.US);
        if (lower.contains("تومان") || lower.contains("toman")) {
            return "toman";
        }
        if (lower.contains("ریال") || lower.contains("rial") || lower.contains("irr")) {
            return "rial";
        }
        return "unknown";
    }

    public static String extractReference(String body) {
        Matcher matcher = REFERENCE.matcher(normalizeDigits(body));
        if (matcher.find()) {
            return matcher.group(1).replaceAll("[^0-9]", "");
        }
        return "";
    }

    public static boolean containsCardLast4(String body, String last4) {
        String card = normalizeDigits(last4).replaceAll("[^0-9]", "");
        if (card.length() != 4) {
            return false;
        }
        return normalizeDigits(body).contains(card);
    }

    public static String buildEventId(String sender, String body, long amount, String reference) {
        String seed = safe(sender) + "|" + safe(body) + "|" + amount + "|" + safe(reference);
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(seed.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte b : bytes) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (Exception e) {
            return String.valueOf(seed.hashCode());
        }
    }

    private static long extractAmountByPattern(String text, Pattern pattern) {
        Matcher matcher = pattern.matcher(text);
        while (matcher.find()) {
            long parsed = parseNumber(matcher.group(1));
            if (parsed >= 1000 && parsed <= 1_000_000_000_000L) {
                return parsed;
            }
        }
        return 0;
    }

    private static long parseNumber(String raw) {
        String digits = normalizeDigits(raw).replaceAll("[^0-9]", "");
        if (digits.isEmpty()) {
            return 0;
        }
        try {
            return Long.parseLong(digits);
        } catch (Exception e) {
            return 0;
        }
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
