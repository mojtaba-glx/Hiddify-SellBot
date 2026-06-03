package com.hiddifysellbot.smsverifier;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class PaymentSmsParser {
    private static final Pattern CREDIT_KEYWORD_AMOUNT = Pattern.compile(
            "(?i)(?:مبلغ|واریز|واريز|دریافت|دريافت|نشست|انتقال\\s+از|amount|deposit|credit)[^0-9۰-۹٠-٩+＋]{0,80}([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{2,24})"
    );
    private static final Pattern DEPOSIT_WITH_RIAL = Pattern.compile(
            "(?i)(?:واریز|واريز|نشست|دریافت|دريافت)[\\s\\S]{0,120}?([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{2,24})\\s*(?:ریال|ريال|rial|irr)"
    );
    private static final Pattern PLUS_BEFORE_AMOUNT = Pattern.compile(
            "[+＋]\\s*([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{2,24})"
    );
    private static final Pattern PLUS_AFTER_AMOUNT = Pattern.compile(
            "([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{2,24})\\s*[+＋]"
    );
    private static final Pattern TRANSFER_AFTER_CARD_LAST4 = Pattern.compile(
            "(?:از\\s*)?کارت[^0-9]{0,12}[0-9]{4}\\s*(?:\\r?\\n|\\s{2,})\\s*([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{2,24})\\s*[+＋]?"
    );
    private static final Pattern ANY_NUMBER = Pattern.compile("([0-9۰-۹٠-٩][0-9۰-۹٠-٩,،.\\u00A0 ]{3,24})");
    private static final Pattern REFERENCE = Pattern.compile(
            "(?i)(?:پیگیری|رهگیری|مرجع|ارجاع|شناسه|reference|ref|trace)[^0-9۰-۹٠-٩]{0,30}([0-9۰-۹٠-٩]{4,30})"
    );
    private static final Pattern MIDDLE_EAST_REFERENCE = Pattern.compile(
            "(?:^|\\n)\\s*([0-9]{2,4}/[0-9]{6,20})\\s*(?:\\n|$)"
    );
    private static final Pattern CARD_LAST4 = Pattern.compile(
            "(?:از\\s*)?کارت[^0-9]{0,12}([0-9]{4})(?![0-9])"
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
        long amount = extractAmountByPattern(text, PLUS_BEFORE_AMOUNT);
        if (amount > 0) {
            return amount;
        }
        amount = extractAmountByPattern(text, PLUS_AFTER_AMOUNT);
        if (amount > 0) {
            return amount;
        }
        amount = extractAmountByPattern(text, TRANSFER_AFTER_CARD_LAST4);
        if (amount > 0) {
            return amount;
        }
        amount = extractAmountByPattern(text, DEPOSIT_WITH_RIAL);
        if (amount > 0) {
            return amount;
        }
        amount = extractAmountByPattern(text, CREDIT_KEYWORD_AMOUNT);
        if (amount > 0) {
            return amount;
        }

        Matcher matcher = ANY_NUMBER.matcher(text);
        long best = 0;
        while (matcher.find()) {
            if (hasIgnoredNumberContext(text, matcher.start(), matcher.end())) {
                continue;
            }
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
        if (lower.contains("بانک خاورمیانه") || lower.contains("بانک خاورميانه") || lower.contains("بلو")) {
            return "rial";
        }
        return "unknown";
    }

    public static String extractReference(String body) {
        String text = normalizeDigits(body);
        Matcher matcher = REFERENCE.matcher(text);
        if (matcher.find()) {
            return matcher.group(1).replaceAll("[^0-9]", "");
        }
        matcher = MIDDLE_EAST_REFERENCE.matcher(text);
        if (matcher.find()) {
            return matcher.group(1).replaceAll("[^0-9]", "");
        }
        return "";
    }

    public static String extractCardLast4(String body) {
        Matcher matcher = CARD_LAST4.matcher(normalizeDigits(body));
        if (matcher.find()) {
            return matcher.group(1).replaceAll("[^0-9]", "");
        }
        return "";
    }

    public static boolean isIncomingPayment(String body) {
        String text = normalizeDigits(body).toLowerCase(Locale.US);
        boolean credit = text.contains("+")
                || text.contains("＋")
                || text.contains("واریز")
                || text.contains("واريز")
                || text.contains("نشست")
                || text.contains("دریافت")
                || text.contains("دريافت")
                || text.contains("انتقال از")
                || text.contains("deposit")
                || text.contains("credit");
        boolean debit = text.contains("برداشت")
                || text.contains("خرید")
                || text.contains("خريد")
                || text.contains("کسر")
                || text.contains("انتقال به")
                || text.contains("debit")
                || text.contains("purchase");
        return credit || !debit;
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

    private static boolean hasIgnoredNumberContext(String text, int start, int end) {
        int beforeStart = Math.max(0, start - 24);
        int afterEnd = Math.min(text.length(), end + 8);
        String before = text.substring(beforeStart, start);
        String numberText = text.substring(start, end);
        String after = text.substring(end, afterEnd);
        if (before.contains("موجودی") || before.contains("مانده") || before.toLowerCase(Locale.US).contains("balance")) {
            return true;
        }
        if (before.contains("کارت") && parseNumber(numberText) >= 1000 && parseNumber(numberText) <= 9999) {
            return true;
        }
        return numberText.contains("/") || after.startsWith("/");
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
