package com.hiddifysellbot.smsverifier;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
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
    private Spinner themeSpinner;
    private Spinner bankSpinner;
    private CheckBox bankEnabledBox;
    private EditText bankSenderInput;
    private EditText bankSampleInput;
    private TextView bankSummaryView;
    private EditText manualSenderInput;
    private EditText manualBodyInput;
    private LinearLayout bankSmsPanel;
    private LinearLayout bankSmsListView;
    private TextView historyView;

    private int bgColor;
    private int cardColor;
    private int inputColor;
    private int textColor;
    private int mutedColor;
    private int strokeColor;
    private int primaryColor;
    private int approvedColor;
    private int rejectedColor;
    private int neutralColor;
    private boolean bankSmsPanelVisible = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        loadPalette();
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
        scrollView.setBackgroundColor(bgColor);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(12), dp(12), dp(12), dp(20));
        root.setBackgroundColor(bgColor);
        scrollView.addView(root);

        TextView title = new TextView(this);
        title.setText("🛡️ SellBot SMS Verifier v" + BuildConfig.VERSION_NAME);
        title.setTextSize(20);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(textColor);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap());

        TextView desc = new TextView(this);
        desc.setText("پردازش SMS بانک، تایید خودکار پرداخت و گزارش شفاف برای فروشگاه شما.");
        desc.setTextSize(12);
        desc.setTextColor(mutedColor);
        desc.setGravity(Gravity.CENTER);
        desc.setPadding(0, dp(6), 0, dp(10));
        root.addView(desc, matchWrap());

        LinearLayout dashboard = addCard(root);
        addSectionTitle(dashboard, "⚡ دسترسی سریع");
        addButtonRow(dashboard,
                new String[]{"📩 پیامک‌های بانکی", "🔎 بررسی پیامک‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                toggleBankSmsPanel();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                saveSettings(false);
                                scanInboxNow();
                            }
                        }
                });
        addButtonRow(dashboard,
                new String[]{"🧪 تست اتصال", "🔄 بروزرسانی"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                saveSettings(false);
                                sendTestWebhook();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                refreshHistory();
                            }
                        }
                });

        LinearLayout connectionCard = addCard(root);
        addSectionTitle(connectionCard, "⚙️ تنظیمات اصلی");

        enabledBox = new CheckBox(this);
        enabledBox.setText("پردازش خودکار SMS فعال باشد");
        styleCheckBox(enabledBox);
        connectionCard.addView(enabledBox, matchWrap());

        themeSpinner = addSpinner(connectionCard, "🎨 تم برنامه", new String[]{"سیستم گوشی", "روشن", "تاریک"});
        webhookInput = addInput(connectionCard, "Webhook URL ربات", "https://example.com/payment/sms-webhook", false, 1);
        secretInput = addInput(connectionCard, "Secret Key اتصال", "کلید امنیتی مشترک با ربات", true, 1);

        cardLast4Box = new CheckBox(this);
        cardLast4Box.setText("اگر SMS چهار رقم کارت مشتری داشت، به ربات ارسال شود");
        styleCheckBox(cardLast4Box);
        connectionCard.addView(cardLast4Box, matchWrap());

        Button saveButton = new Button(this);
        saveButton.setText("💾 ذخیره تنظیمات اصلی");
        styleButton(saveButton, true);
        connectionCard.addView(saveButton, matchWrap());
        saveButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings(true);
            }
        });

        LinearLayout bankCard = addCard(root);
        addSectionTitle(bankCard, "🏦 بانک‌ها و نمونه SMS");
        TextView bankHelp = new TextView(this);
        bankHelp.setText("هر بانک را جدا فعال کن؛ برای هرکدام ۲ تا ۳ سرشماره یا بیشتر و یک نمونه SMS واقعی ذخیره کن.");
        bankHelp.setTextSize(12);
        bankHelp.setTextColor(mutedColor);
        bankHelp.setPadding(0, 0, 0, dp(8));
        bankCard.addView(bankHelp, matchWrap());

        SettingsStore bankSettings = new SettingsStore(this);
        String[] bankNames = new String[bankSettings.getBankCount()];
        for (int i = 0; i < bankNames.length; i++) {
            bankNames[i] = bankSettings.getBankName(i);
        }
        bankSpinner = addSpinner(bankCard, "انتخاب بانک", bankNames);

        bankEnabledBox = new CheckBox(this);
        bankEnabledBox.setText("این بانک فعال باشد");
        styleCheckBox(bankEnabledBox);
        bankCard.addView(bankEnabledBox, matchWrap());

        bankSenderInput = addInput(bankCard, "سرشماره‌های SMS همین بانک", "مثال: 20004861، 3000...\nهر خط یا کاما یک سرشماره", false, 3);
        bankSampleInput = addInput(bankCard, "نمونه SMS همین بانک", "نمونه پیامک واریز همین بانک را اینجا paste کن", false, 4);

        Button saveBankButton = new Button(this);
        saveBankButton.setText("✅ ذخیره بانک انتخاب‌شده");
        styleButton(saveBankButton, true);
        bankCard.addView(saveBankButton, matchWrap());
        saveBankButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSelectedBank();
            }
        });

        bankSummaryView = new TextView(this);
        bankSummaryView.setTextSize(12);
        bankSummaryView.setTextIsSelectable(true);
        bankSummaryView.setTextColor(textColor);
        bankSummaryView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleRounded(bankSummaryView, inputColor, strokeColor, dp(12));
        bankCard.addView(bankSummaryView, matchWrap());

        bankSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                loadSelectedBank();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        LinearLayout testCard = addCard(root);
        addSectionTitle(testCard, "🧪 تست دستی SMS");
        manualSenderInput = addInput(testCard, "سرشماره SMS", "مثال: 20004861", false, 1);
        manualBodyInput = addInput(testCard, "متن SMS بانک", "متن پیامک بانک را برای تست اینجا paste کن", false, 4);

        Button manualSmsButton = new Button(this);
        manualSmsButton.setText("بررسی متن SMS تستی");
        styleButton(manualSmsButton, false);
        testCard.addView(manualSmsButton, matchWrap());
        manualSmsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings(false);
                sendManualSmsTest();
            }
        });

        bankSmsPanel = addCard(root);
        addSectionTitle(bankSmsPanel, "📩 پیامک‌های بانکی");
        TextView smsHelp = new TextView(this);
        smsHelp.setText("پیامک‌های تاییدشده با ✅ و پیامک‌های تاییدنشد‌ه با ❌ نمایش داده می‌شوند.");
        smsHelp.setTextColor(mutedColor);
        smsHelp.setTextSize(12);
        smsHelp.setPadding(0, 0, 0, dp(8));
        bankSmsPanel.addView(smsHelp, matchWrap());
        bankSmsListView = new LinearLayout(this);
        bankSmsListView.setOrientation(LinearLayout.VERTICAL);
        bankSmsPanel.addView(bankSmsListView, matchWrap());
        bankSmsPanel.setVisibility(View.GONE);

        LinearLayout logCard = addCard(root);
        addSectionTitle(logCard, "📋 لاگ کامل برنامه");
        TextView logHelp = new TextView(this);
        logHelp.setText("لاگ‌های فنی و خطاها این پایین می‌مانند؛ بخش پیامک‌ها از دکمه بالا باز می‌شود.");
        logHelp.setTextColor(mutedColor);
        logHelp.setTextSize(12);
        logHelp.setPadding(0, 0, 0, dp(8));
        logCard.addView(logHelp, matchWrap());

        LinearLayout logRow = new LinearLayout(this);
        logRow.setOrientation(LinearLayout.HORIZONTAL);
        logCard.addView(logRow, matchWrap());

        Button refreshButton = new Button(this);
        refreshButton.setText("بروزرسانی لاگ");
        styleButton(refreshButton, false);
        logRow.addView(refreshButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        refreshButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshHistory();
            }
        });

        Button clearButton = new Button(this);
        clearButton.setText("پاک کردن لاگ");
        styleButton(clearButton, false);
        logRow.addView(clearButton, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        clearButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                HistoryStore.clear(MainActivity.this);
                refreshHistory();
            }
        });

        historyView = new TextView(this);
        historyView.setTextSize(11);
        historyView.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        historyView.setTextIsSelectable(true);
        historyView.setTextColor(textColor);
        historyView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleRounded(historyView, inputColor, strokeColor, dp(12));
        logCard.addView(historyView, matchWrap());

        setContentView(scrollView);
    }

    private void loadSettings() {
        SettingsStore settings = new SettingsStore(this);
        enabledBox.setChecked(settings.isEnabled());
        webhookInput.setText(settings.getWebhookUrl());
        secretInput.setText(settings.getSecret());
        cardLast4Box.setChecked(settings.isCardLast4Enabled());
        themeSpinner.setSelection(themeIndex(settings.getThemeMode()));
        loadSelectedBank();
        refreshBankSummary();
        refreshHistory();
    }

    private void saveSettings(boolean showToast) {
        SettingsStore settings = new SettingsStore(this);
        String oldTheme = settings.getThemeMode();
        String newTheme = selectedThemeMode();
        settings.save(
                enabledBox.isChecked(),
                text(webhookInput),
                text(secretInput),
                settings.getSenderFiltersRaw(),
                cardLast4Box.isChecked()
        );
        settings.saveThemeMode(newTheme);
        if (showToast) {
            Toast.makeText(this, "تنظیمات ذخیره شد", Toast.LENGTH_SHORT).show();
        }
        if (!oldTheme.equals(newTheme)) {
            recreate();
        }
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
            Toast.makeText(this, "اجازه READ_SMS را بده و دوباره دکمه بررسی پیامک‌ها را بزن", Toast.LENGTH_LONG).show();
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
                        bankSmsPanelVisible = true;
                        bankSmsPanel.setVisibility(View.VISIBLE);
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
                        bankSmsPanelVisible = true;
                        bankSmsPanel.setVisibility(View.VISIBLE);
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
        renderBankSmsBubbles();
        String history = HistoryStore.get(this);
        historyView.setText(history == null || history.trim().isEmpty()
                ? "هنوز لاگی ثبت نشده است."
                : history);
    }

    private void toggleBankSmsPanel() {
        bankSmsPanelVisible = !bankSmsPanelVisible;
        bankSmsPanel.setVisibility(bankSmsPanelVisible ? View.VISIBLE : View.GONE);
        refreshHistory();
    }

    private void renderBankSmsBubbles() {
        if (bankSmsListView == null) {
            return;
        }
        bankSmsListView.removeAllViews();
        HistoryStore.Entry[] entries = HistoryStore.getBankSmsEntries(this);
        if (entries.length == 0) {
            TextView empty = new TextView(this);
            empty.setText("هنوز پیامک بانکی پردازش‌شده‌ای ثبت نشده است.");
            empty.setTextColor(mutedColor);
            empty.setTextSize(12);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(dp(10), dp(14), dp(10), dp(14));
            bankSmsListView.addView(empty, matchWrap());
            return;
        }
        for (HistoryStore.Entry entry : entries) {
            addSmsBubble(bankSmsListView, entry);
        }
    }

    private void addSmsBubble(LinearLayout parent, HistoryStore.Entry entry) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(entry.approved ? Gravity.RIGHT : Gravity.LEFT);
        row.setPadding(0, dp(5), 0, dp(5));
        parent.addView(row, matchWrap());

        TextView bubble = new TextView(this);
        String mark = entry.approved ? "✅" : (entry.rejected ? "❌" : "•");
        bubble.setText(mark + " " + entry.title + "\n" + compactSmsDetail(entry.detail) + "\n🕒 " + entry.time);
        bubble.setTextSize(12);
        bubble.setTextColor(textColor);
        bubble.setTextIsSelectable(true);
        bubble.setLineSpacing(0, 1.08f);
        bubble.setPadding(dp(12), dp(10), dp(12), dp(10));
        int fill = entry.approved ? approvedColor : (entry.rejected ? rejectedColor : neutralColor);
        styleRounded(bubble, fill, strokeColor, dp(18));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                (int) (getResources().getDisplayMetrics().widthPixels * 0.82f),
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        row.addView(bubble, lp);
    }

    private String compactSmsDetail(String detail) {
        if (detail == null || detail.trim().isEmpty()) {
            return "جزئیات ثبت نشده است.";
        }
        String text = detail.trim();
        text = text.replace("\n🌐 کد HTTP:", "\nکد HTTP:");
        if (text.length() > 520) {
            return text.substring(0, 520) + "...";
        }
        return text;
    }

    private Spinner addSpinner(LinearLayout root, String label, String[] items) {
        TextView labelView = new TextView(this);
        labelView.setText(label);
        labelView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        labelView.setTextColor(textColor);
        labelView.setTextSize(12);
        labelView.setPadding(0, dp(10), 0, dp(2));
        root.addView(labelView, matchWrap());

        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, items);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        root.addView(spinner, matchWrap());
        return spinner;
    }

    private EditText addInput(LinearLayout root, String label, String hint, boolean secret, int minLines) {
        TextView labelView = new TextView(this);
        labelView.setText(label);
        labelView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        labelView.setTextColor(textColor);
        labelView.setTextSize(12);
        labelView.setPadding(0, dp(10), 0, dp(2));
        root.addView(labelView, matchWrap());

        EditText input = new EditText(this);
        input.setHint(hint);
        input.setSingleLine(minLines <= 1);
        input.setMinLines(minLines);
        input.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        input.setTextSize(13);
        input.setTextColor(textColor);
        input.setHintTextColor(mutedColor);
        input.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        input.setPadding(dp(10), dp(7), dp(10), dp(7));
        styleRounded(input, inputColor, strokeColor, dp(10));
        input.setInputType(secret
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        root.addView(input, matchWrap());
        return input;
    }

    private LinearLayout addCard(LinearLayout root) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(12), dp(10), dp(12), dp(12));
        styleRounded(card, cardColor, strokeColor, dp(16));
        LinearLayout.LayoutParams lp = matchWrap();
        lp.setMargins(0, dp(8), 0, dp(8));
        root.addView(card, lp);
        return card;
    }

    private void addButtonRow(LinearLayout root, String[] labels, View.OnClickListener[] listeners) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(3), 0, dp(3));
        root.addView(row, matchWrap());
        for (int i = 0; i < labels.length; i++) {
            Button button = new Button(this);
            button.setText(labels[i]);
            styleButton(button, i == 0);
            button.setOnClickListener(listeners[i]);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
            lp.setMargins(dp(3), 0, dp(3), 0);
            row.addView(button, lp);
        }
    }

    private void addSectionTitle(LinearLayout root, String text) {
        TextView section = new TextView(this);
        section.setText(text);
        section.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        section.setTextColor(textColor);
        section.setTextSize(15);
        section.setPadding(0, dp(3), 0, dp(7));
        root.addView(section, matchWrap());
    }

    private void styleButton(Button button, boolean primary) {
        button.setAllCaps(false);
        button.setTextSize(12);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        button.setTextColor(primary ? Color.WHITE : textColor);
        button.setMinHeight(dp(38));
        button.setMinWidth(0);
        button.setPadding(dp(8), dp(4), dp(8), dp(4));
        styleRounded(button, primary ? primaryColor : inputColor, primary ? primaryColor : strokeColor, dp(12));
    }

    private void styleCheckBox(CheckBox box) {
        box.setTextSize(13);
        box.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        box.setTextColor(textColor);
        box.setPadding(0, dp(2), 0, dp(2));
    }

    private void styleRounded(View view, int fill, int stroke, int radius) {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(fill);
        bg.setStroke(dp(1), stroke);
        bg.setCornerRadius(radius);
        view.setBackground(bg);
    }

    private void loadPalette() {
        SettingsStore settings = new SettingsStore(this);
        boolean dark = isDarkMode(settings.getThemeMode());
        bgColor = Color.parseColor(dark ? "#0F172A" : "#F6F8FC");
        cardColor = Color.parseColor(dark ? "#111827" : "#FFFFFF");
        inputColor = Color.parseColor(dark ? "#1E293B" : "#FFFFFF");
        textColor = Color.parseColor(dark ? "#E5E7EB" : "#111827");
        mutedColor = Color.parseColor(dark ? "#94A3B8" : "#64748B");
        strokeColor = Color.parseColor(dark ? "#334155" : "#CBD5E1");
        primaryColor = Color.parseColor("#2563EB");
        approvedColor = Color.parseColor(dark ? "#064E3B" : "#DCFCE7");
        rejectedColor = Color.parseColor(dark ? "#7F1D1D" : "#FEE2E2");
        neutralColor = Color.parseColor(dark ? "#42320D" : "#FEF3C7");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(bgColor);
            getWindow().setNavigationBarColor(bgColor);
        }
    }

    private boolean isDarkMode(String mode) {
        if (SettingsStore.THEME_DARK.equals(mode)) {
            return true;
        }
        if (SettingsStore.THEME_LIGHT.equals(mode)) {
            return false;
        }
        int night = getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK;
        return night == Configuration.UI_MODE_NIGHT_YES;
    }

    private int themeIndex(String mode) {
        if (SettingsStore.THEME_LIGHT.equals(mode)) {
            return 1;
        }
        if (SettingsStore.THEME_DARK.equals(mode)) {
            return 2;
        }
        return 0;
    }

    private String selectedThemeMode() {
        int pos = themeSpinner == null ? 0 : themeSpinner.getSelectedItemPosition();
        if (pos == 1) {
            return SettingsStore.THEME_LIGHT;
        }
        if (pos == 2) {
            return SettingsStore.THEME_DARK;
        }
        return SettingsStore.THEME_SYSTEM;
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
