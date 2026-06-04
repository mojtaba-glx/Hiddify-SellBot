package com.hiddifysellbot.smsverifier;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

public class SmsMonitorService extends Service {
    private static final String CHANNEL_ID = "sellbot_sms_monitor";
    private static final int NOTIFICATION_ID = 2107;

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
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
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
