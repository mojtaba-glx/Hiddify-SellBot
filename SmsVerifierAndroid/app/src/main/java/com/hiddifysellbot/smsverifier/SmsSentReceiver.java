package com.hiddifysellbot.smsverifier;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * گیرنده نتیجه ارسال SMS (الزام تکمیلی ROLE_SMS).
 * اپ پیامک ارسالی ندارد؛ فقط باید result code را بپذیرد تا سیستم
 * هنگام بررسی کاندیداهای پیامک پیش‌فرض خطا نگیرد.
 */
public final class SmsSentReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        // noop — نتیجه ارسال برای اپ مهم نیست
    }
}
