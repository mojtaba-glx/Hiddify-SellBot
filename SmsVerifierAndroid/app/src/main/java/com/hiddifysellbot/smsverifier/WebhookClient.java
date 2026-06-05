package com.hiddifysellbot.smsverifier;

import java.io.BufferedReader;
import java.io.OutputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

public final class WebhookClient {
    public static final class Result {
        public boolean ok;
        public int statusCode;
        public String body;
        public String error;
    }

    private WebhookClient() {
    }

    public static Result post(String webhookUrl, String secret, SmsEvent event) {
        Result result = new Result();
        HttpURLConnection connection = null;
        try {
            byte[] payload = event.toJson().getBytes(StandardCharsets.UTF_8);
            URL url = new URL(SettingsStore.normalizeWebhookUrl(webhookUrl));
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(15000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json,text/plain,*/*");
            connection.setRequestProperty("User-Agent", "SellBotSmsVerifier/1.0");
            connection.setRequestProperty("X-SellBot-Sms-Secret", SettingsStore.normalizeSecret(secret));
            connection.setRequestProperty("X-SellBot-Event-Id", event.eventId == null ? "" : event.eventId);
            try (OutputStream os = connection.getOutputStream()) {
                os.write(payload);
            }

            result.statusCode = connection.getResponseCode();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                    result.statusCode >= 200 && result.statusCode < 400
                            ? connection.getInputStream()
                            : connection.getErrorStream(),
                    StandardCharsets.UTF_8
            ));
            StringBuilder body = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                body.append(line).append('\n');
            }
            result.body = body.toString().trim();
            result.ok = result.statusCode >= 200 && result.statusCode < 300;
        } catch (Exception e) {
            result.ok = false;
            result.error = e.getClass().getSimpleName() + ": " + e.getMessage();
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
        return result;
    }

    public static String statusLabel(Result result) {
        if (result == null) {
            return "FAILED";
        }
        String body = compactResponse(result.body);
        if (body.contains("\"status\":\"sms_reused\"")) {
            return "SMS_REUSED";
        }
        if (body.contains("\"status\":\"no_pending_match\"")) {
            return "NO_PENDING_MATCH";
        }
        if (body.contains("\"error\":\"ambiguous_pending_payments\"")) {
            return "AMBIGUOUS";
        }
        if (body.contains("\"status\":\"approved\"")
                || body.contains("\"matched\":true")
                || (body.contains("\"matched_payment_id\":") && !body.contains("\"matched_payment_id\":0"))) {
            if (body.contains("\"duplicate\":true") && !body.contains("\"retry\":true")) {
                return "APPROVED_DUPLICATE";
            }
            return "APPROVED";
        }
        if (result.ok) {
            return "SENT";
        }
        return "FAILED";
    }

    public static String compactResponse(String value) {
        return (value == null ? "" : value)
                .toLowerCase(Locale.US)
                .replaceAll("\\s+", "");
    }

    public static String persianStatus(Result result) {
        String label = statusLabel(result);
        if ("APPROVED".equals(label)) {
            return "✅ تایید شد و به ربات اعلام شد";
        }
        if ("APPROVED_DUPLICATE".equals(label)) {
            return "🟡 این SMS قبلاً تایید شده بود؛ تایید جدید انجام نشد";
        }
        if ("NO_PENDING_MATCH".equals(label)) {
            return "🟡 SMS خوانده شد، اما پرداخت pending با این مبلغ پیدا نشد";
        }
        if ("SMS_REUSED".equals(label)) {
            return "🟡 این SMS قبلاً برای پرداخت دیگری استفاده شده؛ بررسی ادمین لازم است";
        }
        if ("AMBIGUOUS".equals(label)) {
            return "🟠 چند پرداخت با مبلغ مشابه پیدا شد؛ ادمین باید بررسی کند";
        }
        if ("SENT".equals(label)) {
            return "📨 به ربات ارسال شد";
        }
        return friendlyError(result);
    }

    public static String friendlyError(Result result) {
        if (result == null) {
            return "🔴 خطای نامشخص";
        }
        String error = result.error == null ? "" : result.error;
        if (result.statusCode == 0) {
            if (error.contains("UnknownHostException")) {
                return "🔴 اینترنت یا DNS مشکل دارد؛ دامنه Webhook پیدا نشد";
            }
            if (error.contains("SocketTimeoutException")) {
                return "🔴 ارتباط با سرور دیر جواب داد؛ اینترنت یا سرور را بررسی کن";
            }
            if (error.contains("SSL")) {
                return "🔴 خطای SSL؛ دامنه یا گواهی HTTPS را بررسی کن";
            }
            if (error.contains("MalformedURLException")) {
                return "🔴 آدرس Webhook اشتباه است";
            }
            if (!error.trim().isEmpty()) {
                return "🔴 خطای ارتباط: " + error;
            }
        }
        if (result.statusCode == 401 || result.statusCode == 403) {
            return "🔴 Secret Key اشتباه است یا تایید SMS در ربات خاموش است";
        }
        if (result.statusCode == 404) {
            return "🔴 مسیر Webhook پیدا نشد؛ آدرس را بررسی کن";
        }
        if (result.statusCode >= 500) {
            return "🔴 خطای سرور ربات؛ لاگ سرور را بررسی کن";
        }
        return "🔴 ارسال ناموفق بود";
    }
}
