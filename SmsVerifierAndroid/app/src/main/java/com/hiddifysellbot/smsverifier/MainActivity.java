package com.hiddifysellbot.smsverifier;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int REQ_SMS_PERMISSION = 1001;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private CheckBox enabledBox;
    private EditText webhookInput;
    private EditText secretInput;
    private EditText senderInput;
    private CheckBox cardLast4Box;
    private EditText cardLast4Input;
    private TextView historyView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        loadSettings();
        requestSmsPermission();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshHistory();
    }

    private void buildUi() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(16), dp(16), dp(24));
        scrollView.addView(root);

        TextView title = new TextView(this);
        title.setText("SellBot SMS Verifier");
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap());

        TextView desc = new TextView(this);
        desc.setText("این اپ فقط SMS سرشماره‌های مشخص‌شده را بررسی می‌کند و نتیجه را به Webhook ربات می‌فرستد.");
        desc.setTextSize(14);
        desc.setPadding(0, dp(8), 0, dp(12));
        root.addView(desc, matchWrap());

        enabledBox = new CheckBox(this);
        enabledBox.setText("فعال‌سازی پردازش خودکار SMS");
        root.addView(enabledBox, matchWrap());

        webhookInput = addInput(root, "Webhook URL ربات", "https://example.com/payment/sms-webhook", false, 1);
        secretInput = addInput(root, "Secret Key اتصال", "کلید امنیتی مشترک با ربات", true, 1);
        senderInput = addInput(root, "سرشماره‌های مجاز بانک", "مثال: BankMellat, 30001234", false, 3);

        cardLast4Box = new CheckBox(this);
        cardLast4Box.setText("فیلتر چهار رقم کارت داخل SMS فعال باشد");
        root.addView(cardLast4Box, matchWrap());

        cardLast4Input = addInput(root, "چهار رقم کارت مورد انتظار", "1234", false, 1);
        cardLast4Input.setInputType(InputType.TYPE_CLASS_NUMBER);
        cardLast4Input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(4)});

        Button saveButton = new Button(this);
        saveButton.setText("ذخیره تنظیمات");
        root.addView(saveButton, matchWrap());
        saveButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
            }
        });

        Button testButton = new Button(this);
        testButton.setText("ارسال تست به Webhook");
        root.addView(testButton, matchWrap());
        testButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
                sendTestWebhook();
            }
        });

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        root.addView(row, matchWrap());

        Button refreshButton = new Button(this);
        refreshButton.setText("بروزرسانی گزارش");
        row.addView(refreshButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        refreshButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshHistory();
            }
        });

        Button clearButton = new Button(this);
        clearButton.setText("پاک کردن گزارش");
        row.addView(clearButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        clearButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                HistoryStore.clear(MainActivity.this);
                refreshHistory();
            }
        });

        TextView historyTitle = new TextView(this);
        historyTitle.setText("گزارش SMSهای پردازش‌شده");
        historyTitle.setTypeface(Typeface.DEFAULT_BOLD);
        historyTitle.setTextSize(16);
        historyTitle.setPadding(0, dp(18), 0, dp(6));
        root.addView(historyTitle, matchWrap());

        historyView = new TextView(this);
        historyView.setTextSize(12);
        historyView.setTypeface(Typeface.MONOSPACE);
        historyView.setTextIsSelectable(true);
        historyView.setPadding(dp(8), dp(8), dp(8), dp(8));
        root.addView(historyView, matchWrap());

        setContentView(scrollView);
    }

    private EditText addInput(LinearLayout root, String label, String hint, boolean secret, int minLines) {
        TextView labelView = new TextView(this);
        labelView.setText(label);
        labelView.setTypeface(Typeface.DEFAULT_BOLD);
        labelView.setPadding(0, dp(10), 0, dp(2));
        root.addView(labelView, matchWrap());

        EditText input = new EditText(this);
        input.setHint(hint);
        input.setSingleLine(minLines <= 1);
        input.setMinLines(minLines);
        input.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        input.setInputType(secret
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        root.addView(input, matchWrap());
        return input;
    }

    private void loadSettings() {
        SettingsStore settings = new SettingsStore(this);
        enabledBox.setChecked(settings.isEnabled());
        webhookInput.setText(settings.getWebhookUrl());
        secretInput.setText(settings.getSecret());
        senderInput.setText(settings.getSenderFiltersRaw());
        cardLast4Box.setChecked(settings.isCardLast4Enabled());
        cardLast4Input.setText(settings.getCardLast4());
        refreshHistory();
    }

    private void saveSettings() {
        SettingsStore settings = new SettingsStore(this);
        settings.save(
                enabledBox.isChecked(),
                text(webhookInput),
                text(secretInput),
                text(senderInput),
                cardLast4Box.isChecked(),
                text(cardLast4Input)
        );
        Toast.makeText(this, "تنظیمات ذخیره شد", Toast.LENGTH_SHORT).show();
    }

    private void sendTestWebhook() {
        final SettingsStore settings = new SettingsStore(this);
        if (settings.getWebhookUrl().trim().isEmpty() || settings.getSecret().trim().isEmpty()) {
            Toast.makeText(this, "Webhook URL و Secret را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }

        Toast.makeText(this, "در حال ارسال تست...", Toast.LENGTH_SHORT).show();
        executor.execute(new Runnable() {
            @Override
            public void run() {
                SmsEvent event = new SmsEvent();
                event.sender = "SELLBOT_TEST";
                event.body = "TEST_FROM_SELLBOT_SMS_VERIFIER";
                event.amount = 0;
                event.currency = "test";
                event.reference = "";
                event.cardLast4 = settings.getCardLast4();
                event.cardLast4Required = settings.isCardLast4Enabled();
                event.receivedAt = System.currentTimeMillis();
                event.deviceTime = System.currentTimeMillis();
                event.test = true;
                event.eventId = "test-" + event.deviceTime;

                final WebhookClient.Result result = WebhookClient.post(settings.getWebhookUrl(), settings.getSecret(), event);
                HistoryStore.add(
                        MainActivity.this,
                        result.ok ? "TEST_SENT" : "TEST_FAILED",
                        "HTTP=" + result.statusCode
                                + "\nResponse=" + (result.body == null ? "" : result.body)
                                + "\nError=" + (result.error == null ? "" : result.error)
                );
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshHistory();
                        Toast.makeText(
                                MainActivity.this,
                                result.ok ? "تست ارسال شد" : "تست ناموفق بود",
                                Toast.LENGTH_LONG
                        ).show();
                    }
                });
            }
        });
    }

    private void requestSmsPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECEIVE_SMS}, REQ_SMS_PERMISSION);
        }
    }

    private void refreshHistory() {
        if (historyView == null) {
            return;
        }
        String history = HistoryStore.get(this);
        historyView.setText(history == null || history.trim().isEmpty() ? "هنوز گزارشی ثبت نشده است." : history);
    }

    private static String text(EditText editText) {
        return editText == null || editText.getText() == null ? "" : editText.getText().toString().trim();
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
