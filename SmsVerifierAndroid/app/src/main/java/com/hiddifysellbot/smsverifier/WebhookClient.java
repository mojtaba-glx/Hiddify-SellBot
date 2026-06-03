package com.hiddifysellbot.smsverifier;

import java.io.BufferedReader;
import java.io.OutputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

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
}
