package com.hiddifysellbot.smsverifier;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
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
    private CheckBox cardLast4Box;
    private Spinner bankSpinner;
    private CheckBox bankEnabledBox;
    private EditText bankSenderInput;
    private EditText bankSampleInput;
    private TextView bankSummaryView;
    private EditText manualSenderInput;
    private EditText manualBodyInput;
    private TextView bankSmsHistoryView;
    private TextView approvedHistoryView;
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
        root.setPadding(dp(12), dp(12), dp(12), dp(20));
        scrollView.addView(root);

        TextView title = new TextView(this);
        title.setText("🛡️ SellBot SMS Verifier v" + BuildConfig.VERSION_NAME);
        title.setTextSize(20);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap());

        TextView desc = new TextView(this);
        desc.setText("SMSهای بانکی را می‌خواند، واریزها را به ربات می‌فرستد و نتیجه تایید را همین‌جا گزارش می‌کند.");
        desc.setTextSize(12);
        desc.setPadding(0, dp(8), 0, dp(12));
        root.addView(desc, matchWrap());

        addSectionTitle(root, "⚙️ تنظیمات اتصال");

        enabledBox = new CheckBox(this);
        enabledBox.setText("فعال‌سازی پردازش خودکار SMS");
        enabledBox.setTextSize(13);
        root.addView(enabledBox, matchWrap());

        webhookInput = addInput(root, "Webhook URL ربات", "https://example.com/payment/sms-webhook", false, 1);
        secretInput = addInput(root, "Secret Key اتصال", "کلید امنیتی مشترک با ربات", true, 1);

        cardLast4Box = new CheckBox(this);
        cardLast4Box.setText("اگر SMS چهار رقم کارت مشتری داشت، به ربات ارسال شود");
        cardLast4Box.setTextSize(13);
        root.addView(cardLast4Box, matchWrap());

        Button saveButton = new Button(this);
        saveButton.setText("ذخیره تنظیمات");
        styleButton(saveButton);
        root.addView(saveButton, matchWrap());
        saveButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
            }
        });

        addSectionTitle(root, "🏦 بانک‌ها و نمونه SMS");

        TextView bankHelp = new TextView(this);
        bankHelp.setText("برای هر کارت بانکی، بانک را انتخاب کن، سرشماره‌های SMS آن بانک را وارد کن و یک نمونه SMS واقعی همان بانک را ذخیره کن.");
        bankHelp.setTextSize(12);
        bankHelp.setPadding(0, 0, 0, dp(8));
        root.addView(bankHelp, matchWrap());

        bankSpinner = new Spinner(this);
        final SettingsStore bankSettings = new SettingsStore(this);
        String[] bankNames = new String[bankSettings.getBankCount()];
        for (int i = 0; i < bankNames.length; i++) {
            bankNames[i] = bankSettings.getBankName(i);
        }
        ArrayAdapter<String> bankAdapter = new ArrayAdapter<>(
                this,
                android.R.layout.simple_spinner_item,
                bankNames
        );
        bankAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        bankSpinner.setAdapter(bankAdapter);
        root.addView(bankSpinner, matchWrap());

        bankEnabledBox = new CheckBox(this);
        bankEnabledBox.setText("این بانک فعال باشد");
        bankEnabledBox.setTextSize(13);
        root.addView(bankEnabledBox, matchWrap());

        bankSenderInput = addInput(root, "سرشماره‌های SMS همین بانک", "مثال: 20004861 یا 3000...\nهر خط یا کاما یک سرشماره", false, 3);
        bankSampleInput = addInput(root, "نمونه SMS همین بانک", "یک نمونه پیامک واریز همین بانک را اینجا paste کن", false, 5);

        Button saveBankButton = new Button(this);
        saveBankButton.setText("ذخیره بانک انتخاب‌شده");
        styleButton(saveBankButton);
        root.addView(saveBankButton, matchWrap());
        saveBankButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSelectedBank();
            }
        });

        bankSummaryView = new TextView(this);
        bankSummaryView.setTextSize(12);
        bankSummaryView.setTextIsSelectable(true);
        bankSummaryView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleBox(bankSummaryView, "#EFF6FF", "#93C5FD");
        root.addView(bankSummaryView, matchWrap());

        bankSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                loadSelectedBank();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        addSectionTitle(root, "🧪 تست و ابزارها");

        Button testButton = new Button(this);
        testButton.setText("ارسال تست به Webhook");
        styleButton(testButton);
        root.addView(testButton, matchWrap());
        testButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
                sendTestWebhook();
            }
        });

        Button scanInboxButton = new Button(this);
        scanInboxButton.setText("بررسی پیامک‌های قبلی و ارسال دوباره");
        styleButton(scanInboxButton);
        root.addView(scanInboxButton, matchWrap());
        scanInboxButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
                scanInboxNow();
            }
        });

        manualSenderInput = addInput(root, "تست دستی: سرشماره SMS", "مثال: 20004861", false, 1);
        manualBodyInput = addInput(root, "تست دستی: متن SMS بانک", "متن پیامک بانک را برای تست اینجا paste کن", false, 5);

        Button manualSmsButton = new Button(this);
        manualSmsButton.setText("بررسی متن SMS تستی");
        styleButton(manualSmsButton);
        root.addView(manualSmsButton, matchWrap());
        manualSmsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings();
                sendManualSmsTest();
            }
        });

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        root.addView(row, matchWrap());

        Button refreshButton = new Button(this);
        refreshButton.setText("بروزرسانی گزارش");
        styleButton(refreshButton);
        row.addView(refreshButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        refreshButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshHistory();
            }
        });

        Button clearButton = new Button(this);
        clearButton.setText("پاک کردن گزارش");
        styleButton(clearButton);
        row.addView(clearButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        clearButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                HistoryStore.clear(MainActivity.this);
                refreshHistory();
            }
        });

        addSectionTitle(root, "📩 پیامک‌های بانکی");

        Button bankSmsButton = new Button(this);
        bankSmsButton.setText("نمایش / بروزرسانی پیامک‌های بانکی");
        styleButton(bankSmsButton);
        root.addView(bankSmsButton, matchWrap());
        bankSmsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshHistory();
                Toast.makeText(MainActivity.this, "پیامک‌های بانکی بروزرسانی شد", Toast.LENGTH_SHORT).show();
            }
        });

        bankSmsHistoryView = new TextView(this);
        bankSmsHistoryView.setTextSize(12);
        bankSmsHistoryView.setTextIsSelectable(true);
        bankSmsHistoryView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleBox(bankSmsHistoryView, "#FFF7ED", "#FDBA74");
        root.addView(bankSmsHistoryView, matchWrap());

        addSectionTitle(root, "✅ واریزی‌های تاییدشده");

        approvedHistoryView = new TextView(this);
        approvedHistoryView.setTextSize(12);
        approvedHistoryView.setTextIsSelectable(true);
        approvedHistoryView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleBox(approvedHistoryView, "#ECFDF5", "#86EFAC");
        root.addView(approvedHistoryView, matchWrap());

        addSectionTitle(root, "📋 گزارش کامل پردازش SMS");

        historyView = new TextView(this);
        historyView.setTextSize(12);
        historyView.setTextIsSelectable(true);
        historyView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleBox(historyView, "#F8FAFC", "#CBD5E1");
        root.addView(historyView, matchWrap());

        setContentView(scrollView);
    }

    private EditText addInput(LinearLayout root, String label, String hint, boolean secret, int minLines) {
        TextView labelView = new TextView(this);
        labelView.setText(label);
        labelView.setTypeface(Typeface.DEFAULT_BOLD);
        labelView.setTextSize(12);
        labelView.setPadding(0, dp(10), 0, dp(2));
        root.addView(labelView, matchWrap());

        EditText input = new EditText(this);
        input.setHint(hint);
        input.setSingleLine(minLines <= 1);
        input.setMinLines(minLines);
        input.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        input.setTextSize(13);
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
        cardLast4Box.setChecked(settings.isCardLast4Enabled());
        loadSelectedBank();
        refreshBankSummary();
        refreshHistory();
    }

    private void saveSettings() {
        SettingsStore settings = new SettingsStore(this);
        settings.save(
                enabledBox.isChecked(),
                text(webhookInput),
                text(secretInput),
                settings.getSenderFiltersRaw(),
                cardLast4Box.isChecked()
        );
        Toast.makeText(this, "تنظیمات ذخیره شد", Toast.LENGTH_SHORT).show();
    }

    private void loadSelectedBank() {
        if (bankSpinner == null || bankEnabledBox == null || bankSenderInput == null || bankSampleInput == null) {
            return;
        }
        SettingsStore settings = new SettingsStore(this);
        SettingsStore.BankConfig bank = settings.getBank(bankSpinner.getSelectedItemPosition());
        bankEnabledBox.setChecked(bank.enabled);
        bankSenderInput.setText(bank.senderFilters);
        bankSampleInput.setText(bank.sampleSms);
    }

    private void saveSelectedBank() {
        if (bankSpinner == null) {
            return;
        }
        boolean bankEnabled = bankEnabledBox.isChecked();
        String senders = text(bankSenderInput);
        String sample = text(bankSampleInput);
        if (bankEnabled && senders.isEmpty()) {
            Toast.makeText(this, "برای فعال کردن بانک، حداقل یک سرشماره SMS وارد کن", Toast.LENGTH_LONG).show();
            return;
        }
        if (bankEnabled && sample.isEmpty()) {
            Toast.makeText(this, "برای بار اول، یک نمونه SMS واقعی همین بانک را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }

        SettingsStore settings = new SettingsStore(this);
        settings.saveBank(
                bankSpinner.getSelectedItemPosition(),
                bankEnabled,
                senders,
                sample
        );
        refreshBankSummary();
        Toast.makeText(this, "تنظیمات بانک ذخیره شد", Toast.LENGTH_SHORT).show();
    }

    private void refreshBankSummary() {
        if (bankSummaryView == null) {
            return;
        }
        SettingsStore settings = new SettingsStore(this);
        bankSummaryView.setText("بانک‌های فعال:\n" + settings.getBanksSummary());
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
                event.cardLast4 = "";
                event.cardLast4Required = false;
                event.receivedAt = System.currentTimeMillis();
                event.deviceTime = System.currentTimeMillis();
                event.test = true;
                event.eventId = "test-" + event.deviceTime;

                final WebhookClient.Result result = WebhookClient.post(settings.getWebhookUrl(), settings.getSecret(), event);
                HistoryStore.add(
                        MainActivity.this,
                        result.ok ? "TEST_SENT" : "TEST_FAILED",
                        "📨 نتیجه تست: " + WebhookClient.persianStatus(result)
                                + "\n🌐 کد HTTP: " + result.statusCode
                                + "\n🧾 پاسخ ربات: " + (result.body == null || result.body.trim().isEmpty() ? "-" : result.body)
                                + "\n⚠️ خطا: " + (result.error == null || result.error.trim().isEmpty() ? "-" : result.error)
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
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            boolean receiveDenied = checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED;
            boolean readDenied = checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED;
            if (receiveDenied || readDenied) {
                requestPermissions(
                        new String[]{Manifest.permission.RECEIVE_SMS, Manifest.permission.READ_SMS},
                        REQ_SMS_PERMISSION
                );
            }
        }
    }

    private void scanInboxNow() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestSmsPermission();
            Toast.makeText(this, "اجازه READ_SMS را بده و دوباره دکمه بررسی پیامک‌های قبلی را بزن", Toast.LENGTH_LONG).show();
            return;
        }

        Toast.makeText(this, "در حال بررسی پیامک‌های قبلی...", Toast.LENGTH_SHORT).show();
        executor.execute(new Runnable() {
            @Override
            public void run() {
                final int scanned = SmsInboxScanner.scanRecent(MainActivity.this);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshHistory();
                        Toast.makeText(
                                MainActivity.this,
                                "بررسی تمام شد: " + scanned + " پیامک",
                                Toast.LENGTH_LONG
                        ).show();
                    }
                });
            }
        });
    }

    private void sendManualSmsTest() {
        final String sender = text(manualSenderInput);
        final String body = text(manualBodyInput);
        if (sender.isEmpty() || body.isEmpty()) {
            Toast.makeText(this, "سرشماره و متن SMS تستی را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }

        Toast.makeText(this, "در حال بررسی متن SMS تستی...", Toast.LENGTH_SHORT).show();
        executor.execute(new Runnable() {
            @Override
            public void run() {
                SmsProcessor.handleIncomingSms(MainActivity.this, sender, body, System.currentTimeMillis());
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshHistory();
                        Toast.makeText(MainActivity.this, "تست دستی انجام شد", Toast.LENGTH_LONG).show();
                    }
                });
            }
        });
    }

    private void refreshHistory() {
        if (historyView == null) {
            return;
        }
        refreshBankSummary();
        String history = HistoryStore.get(this);
        String approved = HistoryStore.getApproved(this);
        String bankSms = HistoryStore.getBankSms(this);
        if (bankSmsHistoryView != null) {
            bankSmsHistoryView.setText(bankSms == null || bankSms.trim().isEmpty()
                    ? "هنوز پیامک بانکی پردازش‌شده‌ای ثبت نشده است."
                    : bankSms);
        }
        if (approvedHistoryView != null) {
            approvedHistoryView.setText(approved == null || approved.trim().isEmpty()
                    ? "هنوز واریزی تاییدشده‌ای ثبت نشده است."
                    : approved);
        }
        historyView.setText(history == null || history.trim().isEmpty()
                ? "هنوز گزارشی ثبت نشده است."
                : history);
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

    private void addSectionTitle(LinearLayout root, String text) {
        TextView section = new TextView(this);
        section.setText(text);
        section.setTypeface(Typeface.DEFAULT_BOLD);
        section.setTextSize(15);
        section.setPadding(0, dp(16), 0, dp(6));
        root.addView(section, matchWrap());
    }

    private void styleButton(Button button) {
        button.setAllCaps(false);
        button.setTextSize(12);
        button.setMinHeight(dp(38));
        button.setMinWidth(0);
        button.setPadding(dp(8), dp(4), dp(8), dp(4));
    }

    private void styleBox(TextView view, String fill, String stroke) {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor(fill));
        bg.setStroke(dp(1), Color.parseColor(stroke));
        bg.setCornerRadius(dp(10));
        view.setBackground(bg);
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
