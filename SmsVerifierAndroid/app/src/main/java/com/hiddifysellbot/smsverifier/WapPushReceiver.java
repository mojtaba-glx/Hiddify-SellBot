package com.hiddifysellbot.smsverifier;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * الزام سیستم برای کاندید شدن به‌عنوان «اپ پیش‌فرض پیامک» (ROLE_SMS).
 * WAP_PUSH_DELIVER مربوط به MMS/دیتای WAP است؛ برای این اپ فقط دریافت
 * SMS بانکی مهم است، پس این گیرنده صرفاً وجود دارد تا سیستم اپ را به‌عنوان
 * کاندیدای پیامک پیش‌فرض بپذیرد و پیامک‌های MMS را بی‌صدا رد می‌کند.
 */
public final class WapPushReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        // MMS/WAP push برای این اپ کاربردی ندارد — عمداً بدون پردازش
        // نتایج را finish می‌کنیم تا بقیه سیستم متوقف نشوند.
        if (getResultData() != null) {
            setResultData(null);
        }
    }
}
