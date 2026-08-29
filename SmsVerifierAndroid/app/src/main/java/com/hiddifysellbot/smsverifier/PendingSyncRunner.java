package com.hiddifysellbot.smsverifier;

import android.content.Context;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * همگام‌سازی هوشمند: پیامک‌های بانکی که هنوز قطعی تایید نشده‌اند (پرداخت pending
 * پیدا نشد / ارسال بدون پاسخ) به‌صورت خودکار دوباره به ربات استعلام می‌شوند؛
 * بنابراین اگر پرداخت بعداً در تلگرام تایید شده باشد، رکورد بدون زدن دستی
 * «همگام‌سازی» به‌تنهایی به فیلتر «تایید شده» منتقل می‌شود و فقط رکوردهایی که
 * واقعاً مشکل دارند در «نیازمند بررسی» می‌مانند.
 * امن است: فقط رکوردهای قابل retry استعلام می‌شوند (تاییدشده و sms_reused هرگز).
 */
public final class PendingSyncRunner {
    private static final int MAX_PER_RUN = 10;
    private static int rotation = 0;

    private PendingSyncRunner() {
    }

    public static int run(Context context) {
        HistoryStore.Entry[] entries = HistoryStore.getBankSmsEntries(context);
        if (entries == null || entries.length == 0) {
            return 0;
        }
        SettingsStore settings = new SettingsStore(context);
        if (!settings.isEnabled() || !settings.canSendWebhook()) {
            return 0;
        }

        // مرحله ۱: همه رکوردهای قابل استعلام را جمع کن
        List<Object[]> tasks = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (HistoryStore.Entry entry : entries) {
            if (entry == null) {
                continue;
            }
            String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
            if (rawSms.isEmpty()) {
                rawSms = extractDetailBlock(entry.detail, "متن SMS:");
            }
            String sender = extractDetailLine(entry.detail, "👤 سرشماره:");
            if (sender.isEmpty()) {
                sender = extractDetailLine(entry.detail, "فرستنده:");
            }
            if (sender.isEmpty()) {
                sender = extractDetailLine(entry.detail, "Sender=");
            }
            if (sender.trim().isEmpty() || rawSms.trim().isEmpty() || !PaymentSmsParser.isIncomingPayment(rawSms)) {
                continue;
            }
            long amount = PaymentSmsParser.extractAmount(rawSms);
            String reference = PaymentSmsParser.extractReference(rawSms);
            String eventId = extractDetailLine(entry.detail, "🔐 شناسه داخلی:");
            if (eventId.isEmpty()) {
                eventId = PaymentSmsParser.buildEventId(sender, rawSms, amount, reference);
            }
            // shouldRetryUnique فقط رکوردهای قابل retry را پاس می‌دهد؛
            // رکورد تاییدشده هرگز دوباره استعلام نمی‌شود
            if (eventId.isEmpty() || seen.contains(eventId) || !HistoryStore.shouldRetryUnique(context, eventId)) {
                continue;
            }
            seen.add(eventId);
            tasks.add(new Object[]{sender, rawSms, parseEntryTimeMillis(entry.time)});
        }
        if (tasks.isEmpty()) {
            return 0;
        }

        // مرحله ۲: نوبت‌گردشی — هر چرخه MAX_PER_RUN تای بعدی، تا همه نوبتشان برسد
        int total = tasks.size();
        int start = Math.abs(rotation) % total;
        rotation++;
        int taken = 0;
        for (int i = 0; i < total && taken < MAX_PER_RUN; i++) {
            Object[] task = tasks.get((start + i) % total);
            SmsProcessor.handleIncomingSms(context, (String) task[0], (String) task[1], (Long) task[2]);
            taken++;
        }
        return taken;
    }

    private static String extractDetailBlock(String detail, String marker) {
        if (detail == null || marker == null || marker.trim().isEmpty()) {
            return "";
        }
        int index = detail.indexOf(marker);
        if (index < 0) {
            return "";
        }
        return detail.substring(index + marker.length()).trim();
    }

    private static String extractDetailLine(String detail, String prefix) {
        if (detail == null || detail.trim().isEmpty()) {
            return "";
        }
        String[] lines = detail.split("\\n");
        for (String line : lines) {
            String text = line == null ? "" : line.trim();
            if (text.startsWith(prefix)) {
                return text.substring(prefix.length()).trim();
            }
        }
        return "";
    }

    private static long parseEntryTimeMillis(String time) {
        if (time == null || time.trim().length() < 10) {
            return System.currentTimeMillis();
        }
        try {
            Date date = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(time.trim());
            return date == null ? System.currentTimeMillis() : date.getTime();
        } catch (Exception ignored) {
            return System.currentTimeMillis();
        }
    }
}
