package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.Date;

public final class BackupStore {
    private static final String SETTINGS_PREF = "sellbot_sms_verifier";
    private static final String HISTORY_PREF = "sellbot_sms_history";
    private static final String INCOME_PREF = IncomeStore.PREF_NAME;
    private static final String[] PREF_NAMES = {SETTINGS_PREF, HISTORY_PREF, INCOME_PREF};

    private BackupStore() {
    }

    public static String suggestedFileName() {
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        return "SellBotSmsVerifier-backup-" + stamp + ".json";
    }

    public static String exportJson(Context context) throws Exception {
        JSONObject root = new JSONObject();
        root.put("app", "SellBotSmsVerifier");
        root.put("format", 1);
        root.put("version", BuildConfig.VERSION_NAME);
        root.put("created_at", new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date()));

        JSONObject prefs = new JSONObject();
        for (String name : PREF_NAMES) {
            prefs.put(name, prefsToJson(context, name));
        }
        root.put("preferences", prefs);
        return root.toString(2);
    }

    public static int importJson(Context context, String json) throws Exception {
        JSONObject root = new JSONObject(json == null ? "" : json);
        JSONObject prefs = root.getJSONObject("preferences");
        int restored = 0;
        for (String name : PREF_NAMES) {
            if (!prefs.has(name)) {
                continue;
            }
            jsonToPrefs(context, name, prefs.getJSONObject(name));
            restored++;
        }
        return restored;
    }

    private static JSONObject prefsToJson(Context context, String prefName) throws Exception {
        SharedPreferences prefs = context.getApplicationContext().getSharedPreferences(prefName, Context.MODE_PRIVATE);
        JSONObject out = new JSONObject();
        Map<String, ?> all = prefs.getAll();
        for (Map.Entry<String, ?> item : all.entrySet()) {
            Object value = item.getValue();
            JSONObject wrapped = new JSONObject();
            if (value instanceof Boolean) {
                wrapped.put("type", "boolean");
                wrapped.put("value", value);
            } else if (value instanceof Integer) {
                wrapped.put("type", "int");
                wrapped.put("value", value);
            } else if (value instanceof Long) {
                wrapped.put("type", "long");
                wrapped.put("value", value);
            } else if (value instanceof Float) {
                wrapped.put("type", "float");
                wrapped.put("value", value);
            } else if (value instanceof Set) {
                wrapped.put("type", "string_set");
                JSONArray array = new JSONArray();
                for (Object setItem : (Set<?>) value) {
                    array.put(setItem == null ? "" : String.valueOf(setItem));
                }
                wrapped.put("value", array);
            } else {
                wrapped.put("type", "string");
                wrapped.put("value", value == null ? "" : String.valueOf(value));
            }
            out.put(item.getKey(), wrapped);
        }
        return out;
    }

    private static void jsonToPrefs(Context context, String prefName, JSONObject values) throws Exception {
        SharedPreferences.Editor editor = context.getApplicationContext()
                .getSharedPreferences(prefName, Context.MODE_PRIVATE)
                .edit()
                .clear();
        JSONArray names = values.names();
        if (names != null) {
            for (int i = 0; i < names.length(); i++) {
                String key = names.getString(i);
                JSONObject wrapped = values.getJSONObject(key);
                String type = wrapped.optString("type", "string");
                if ("boolean".equals(type)) {
                    editor.putBoolean(key, wrapped.optBoolean("value", false));
                } else if ("int".equals(type)) {
                    editor.putInt(key, wrapped.optInt("value", 0));
                } else if ("long".equals(type)) {
                    editor.putLong(key, wrapped.optLong("value", 0L));
                } else if ("float".equals(type)) {
                    editor.putFloat(key, (float) wrapped.optDouble("value", 0.0));
                } else if ("string_set".equals(type)) {
                    JSONArray array = wrapped.optJSONArray("value");
                    Set<String> set = new HashSet<>();
                    if (array != null) {
                        for (int j = 0; j < array.length(); j++) {
                            set.add(array.optString(j, ""));
                        }
                    }
                    editor.putStringSet(key, set);
                } else {
                    editor.putString(key, wrapped.optString("value", ""));
                }
            }
        }
        editor.apply();
    }
}
