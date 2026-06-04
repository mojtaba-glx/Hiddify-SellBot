package com.hiddifysellbot.smsverifier;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action) && !"android.intent.action.MY_PACKAGE_REPLACED".equals(action)) {
            return;
        }
        SettingsStore settings = new SettingsStore(context);
        if (settings.isEnabled()) {
            SmsMonitorService.start(context);
        }
    }
}
