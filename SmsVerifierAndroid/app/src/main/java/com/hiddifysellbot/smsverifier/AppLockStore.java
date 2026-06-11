package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

public final class AppLockStore {
    static final String PREF_NAME = "sellbot_sms_security";
    private static final String KEY_ENABLED = "app_lock_enabled";
    private static final String KEY_SALT = "app_lock_salt";
    private static final String KEY_HASH = "app_lock_hash";

    private AppLockStore() {
    }

    public static boolean isEnabled(Context context) {
        SharedPreferences prefs = prefs(context);
        return prefs.getBoolean(KEY_ENABLED, false)
                && !prefs.getString(KEY_HASH, "").trim().isEmpty()
                && !prefs.getString(KEY_SALT, "").trim().isEmpty();
    }

    public static boolean hasPassword(Context context) {
        return !prefs(context).getString(KEY_HASH, "").trim().isEmpty();
    }

    public static boolean setPassword(Context context, String password) {
        String value = safe(password);
        if (value.length() < 4) {
            return false;
        }
        String salt = randomSalt();
        String hash = hash(value, salt);
        prefs(context).edit()
                .putString(KEY_SALT, salt)
                .putString(KEY_HASH, hash)
                .putBoolean(KEY_ENABLED, true)
                .apply();
        return true;
    }

    public static boolean verify(Context context, String password) {
        SharedPreferences prefs = prefs(context);
        String salt = prefs.getString(KEY_SALT, "");
        String hash = prefs.getString(KEY_HASH, "");
        if (salt == null || salt.trim().isEmpty() || hash == null || hash.trim().isEmpty()) {
            return false;
        }
        return constantEquals(hash, hash(safe(password), salt));
    }

    public static void disable(Context context) {
        prefs(context).edit().putBoolean(KEY_ENABLED, false).apply();
    }

    public static void enable(Context context) {
        if (hasPassword(context)) {
            prefs(context).edit().putBoolean(KEY_ENABLED, true).apply();
        }
    }

    public static void clear(Context context) {
        prefs(context).edit()
                .remove(KEY_ENABLED)
                .remove(KEY_SALT)
                .remove(KEY_HASH)
                .apply();
    }

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
    }

    private static String randomSalt() {
        byte[] salt = new byte[18];
        new SecureRandom().nextBytes(salt);
        return Base64.encodeToString(salt, Base64.NO_WRAP);
    }

    private static String hash(String password, String salt) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] out = digest.digest((salt + ":" + password).getBytes(StandardCharsets.UTF_8));
            return Base64.encodeToString(out, Base64.NO_WRAP);
        } catch (Exception e) {
            return "";
        }
    }

    private static boolean constantEquals(String left, String right) {
        byte[] a = safe(left).getBytes(StandardCharsets.UTF_8);
        byte[] b = safe(right).getBytes(StandardCharsets.UTF_8);
        int diff = a.length ^ b.length;
        for (int i = 0; i < Math.min(a.length, b.length); i++) {
            diff |= a[i] ^ b[i];
        }
        return diff == 0;
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }
}
