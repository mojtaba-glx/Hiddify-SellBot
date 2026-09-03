package com.hiddifysellbot.smsverifier;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.database.ContentObserver;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

public class SmsMonitorService extends Service {
    private static final String CHANNEL_ID = "sellbot_sms_monitor";
    private static final int NOTIFICATION_ID = 2107;
    private static final long AUTO_SYNC_INTERVAL_MS = 90L * 1000L;
    private static final long INBOX_SCAN_INTERVAL_MS = 90L * 1000L;
    private static final long OBSERVER_DEBOUNCE_MS = 1500L;

    private final Handler autoSyncHandler = new Handler(Looper.getMainLooper());
    private final Handler inboxScanHandler = new Handler(Looper.getMainLooper());

    private ContentObserver smsObserver;
    private boolean observerRegistered = false;

    // اسکن با تأخیر کوتاه تا پیامک‌های چندتکه در یک فراخوانی جمع شوند
    private final Runnable debouncedInboxScan = new Runnable() {
        @Override
        public void run() {
            triggerInboxScan();
        }
    };

    private final Runnable autoSyncTask = new Runnable() {
        @Override
        public void run() {
            // همگام‌سازی هوشمند خودکار: رکوردهای تایید‌نشده دوباره به ربات استعلام
            // می‌شوند تا وضعیتشان (مثلا تایید در تلگرام) بدون دخالت کاربر بروز شود
            new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        PendingSyncRunner.run(SmsMonitorService.this);
                    } catch (Exception ignored) {
                    }
                }
            }, "sellbot-auto-sync").start();
            autoSyncHandler.postDelayed(this, AUTO_SYNC_INTERVAL_MS);
        }
    };

    // اسکن دوره‌ای جبرانی صندوق: اگر برادکست یا ContentObserver به‌دلیل محدودیت
    // باتری/روم (مثلاً MIUI) از دست برود، این چرخه پیامک‌های جاافتاده را می‌گیرد
    private final Runnable inboxScanTask = new Runnable() {
        @Override
        public void run() {
            triggerInboxScan();
            inboxScanHandler.postDelayed(this, INBOX_SCAN_INTERVAL_MS);
        }
    };

    public static void start(Context context) {
        try {
            Intent intent = new Intent(context.getApplicationContext(), SmsMonitorService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.getApplicationContext().startForegroundService(intent);
            } else {
                context.getApplicationContext().startService(intent);
            }
        } catch (Exception ignored) {
        }
    }

    public static void stop(Context context) {
        context.getApplicationContext().stopService(new Intent(context.getApplicationContext(), SmsMonitorService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        SettingsStore settings = new SettingsStore(this);
        if (!settings.isEnabled()) {
            stopForegroundCompat();
            stopSelf();
            return START_NOT_STICKY;
        }
        try {
            startForegroundCompat();
        } catch (Exception ignored) {
            stopSelf();
            return START_NOT_STICKY;
        }
        registerSmsObserver();
        autoSyncHandler.removeCallbacks(autoSyncTask);
        autoSyncHandler.post(autoSyncTask);
        inboxScanHandler.removeCallbacks(inboxScanTask);
        inboxScanHandler.postDelayed(inboxScanTask, INBOX_SCAN_INTERVAL_MS);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        autoSyncHandler.removeCallbacksAndMessages(null);
        inboxScanHandler.removeCallbacksAndMessages(null);
        unregisterSmsObserver();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /**
     * زنگ‌در روی صندوق پیامک: هر بار که پیامکی (توسط اپ پیامک اصلی) در
     * content://sms درج/به‌روزرسانی شود، اسکن صندوق راه می‌افتد. این روش بدون
     * «اپ پیش‌فرض پیامک» شدن و بدون دسترسی اعلان روی همهٔ نسخه‌ها کار می‌کند.
     */
    private void registerSmsObserver() {
        if (observerRegistered) {
            return;
        }
        try {
            smsObserver = new ContentObserver(autoSyncHandler) {
                @Override
                public void onChange(boolean selfChange, Uri uri) {
                    inboxScanHandler.removeCallbacks(debouncedInboxScan);
                    inboxScanHandler.postDelayed(debouncedInboxScan, OBSERVER_DEBOUNCE_MS);
                }
            };
            getContentResolver().registerContentObserver(Uri.parse("content://sms"), true, smsObserver);
            observerRegistered = true;
        } catch (Exception ignored) {
        }
    }

    private void unregisterSmsObserver() {
        if (!observerRegistered || smsObserver == null) {
            return;
        }
        try {
            getContentResolver().unregisterContentObserver(smsObserver);
        } catch (Exception ignored) {
        }
        observerRegistered = false;
        smsObserver = null;
    }

    private void triggerInboxScan() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    SmsInboxScanner.scanRecent(SmsMonitorService.this, false);
                } catch (Exception ignored) {
                }
            }
        }, "sellbot-inbox-scan").start();
    }

    private void startForegroundCompat() {
        createChannel();
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "SellBot SMS Monitor",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("پایش سبک پیامک‌های بانکی برای تایید خودکار پرداخت");
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.createNotificationChannel(channel);
        }
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 0, openIntent, flags);

        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        builder.setContentTitle("SellBot SMS Verifier فعال است")
                .setContentText("در حال پایش امن پیامک‌های بانکی برای تایید پرداخت")
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .setShowWhen(false);
        return builder.build();
    }
}
