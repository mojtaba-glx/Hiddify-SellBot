package com.hiddifysellbot.smsverifier;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

/**
 * سرویس الزامی برای کاندید شدن اپ به‌عنوان «اپ پیش‌فرض پیامک» (ROLE_SMS).
 * سیستم‌عامل از اندروید ۱۰ به بالا (مهم‌تر روی اندروید ۱۲/۱۳) این اکشن را
 * برای تشخیص کاندیداهای پیامک پیش‌فرض صدا می‌زند؛ بدون این سرویس، اپ در
 * تنظیمات «برنامه پیامک پیش‌فرض» و در دیالوگ RoleManager نمایش داده نمی‌شود
 * و درخواست فعال‌سازی با خطا رد می‌شود.
 */
public final class SmsConfigService extends Service {
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // تنظیم خاصی لازم نیست؛ فقط باید به اکشن android.telephony.action.CONFIGURATION پاسخ دهیم
        if (intent != null) {
            stopSelf(startId);
        }
        return START_NOT_STICKY;
    }
}
