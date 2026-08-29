package com.hiddifysellbot.smsverifier;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

/**
 * سرویس الزامی پنجم برای کاندید شدن به‌عنوان «اپ پیش‌فرض پیامک» (ROLE_SMS).
 * سیستم از این سرویس برای «پاسخ سریع با پیامک» (respond via message) استفاده
 * می‌کند و باید با مجوز SEND_RESPOND_VIA_MESSAGE محافظت شود. اپ ما پیامک پاسخ
 * نمی‌فرستد؛ فقط باید کامپوننت با این فیلتر وجود داشته باشد وگرنه
 * PermissionController اپ را در فهرست کاندیدهای پیامک پیش‌فرض قبول نمی‌کند:
 * "not qualified for android.app.role.SMS due to missing
 * RequiredComponent{mAction='android.intent.action.RESPOND_VIA_MESSAGE',
 * mDataScheme='smsto', mPermission='android.permission.SEND_RESPOND_VIA_MESSAGE'}"
 */
public final class RespondViaMessageService extends Service {
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // پاسخ سریع پیامکی پشتیبانی نمی‌شود؛ فقط وجود کامپوننت برای واجد شرایط بودن لازم است
        if (intent != null) {
            stopSelf(startId);
        }
        return START_NOT_STICKY;
    }
}
