package com.hiddifysellbot.smsverifier;

import android.content.Context;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

/**
 * حالت هم‌زمان: وقتی اپ پیامک اصلی گوشی «اپ پیش‌فرض پیامک» است، این اپ نمی‌تواند
 * مستقیم پیامک بخواند (فقط یک اپ می‌تواند پیش‌فرض باشد). ولی اعلان همان پیامک‌ها
 * از طریق NotificationListenerService قابل خواندن است؛ متن اعلان بانکی مثل متن
 * SMS است و همان پایپ‌لاین (فیلتر سرشماره، تشخیص واریز، وب‌هوک، ددوپ) را طی می‌کند.
 */
public final class PaymentNotificationListener extends NotificationListenerService {

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            processNotification(this, sbn);
        } catch (Exception ignored) {
        }
    }

    private static void processNotification(Context context, StatusBarNotification sbn) {
        if (sbn == null || sbn.getNotification() == null) {
            return;
        }
        SettingsStore settings = new SettingsStore(context);
        if (!settings.isEnabled() || !settings.isNotificationModeEnabled() || !settings.hasSenderFilters()) {
            return;
        }
        // اعلان خود اپ را نادیده بگیر
        if (context.getPackageName().equals(sbn.getPackageName())) {
            return;
        }
        Bundle extras = sbn.getNotification().extras;
        if (extras == null) {
            return;
        }
        CharSequence titleCs = extras.getCharSequence("android.title");
        CharSequence bigCs = extras.getCharSequence("android.bigText");
        CharSequence textCs = extras.getCharSequence("android.text");
        String title = titleCs == null ? "" : titleCs.toString().trim();
        String body = bigCs != null && bigCs.toString().trim().length() > 0
                ? bigCs.toString().trim()
                : (textCs == null ? "" : textCs.toString().trim());
        if (title.isEmpty() || body.isEmpty()) {
            return;
        }
        // عنوان اعلان پیامک معمولاً شماره فرستنده یا نام مخاطب است؛ همه چک‌ها
        // (بانک مجاز، واریزی بودن، ارسال وب‌هوک، ثبت گزارش) در SmsProcessor انجام می‌شود
        SmsProcessor.handleIncomingSms(context, title, body, sbn.getPostTime());
    }
}
