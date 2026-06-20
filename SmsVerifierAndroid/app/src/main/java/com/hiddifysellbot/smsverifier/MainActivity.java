package com.hiddifysellbot.smsverifier;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.PopupWindow;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int REQ_SMS_PERMISSION = 1001;
    private static final int REQ_NOTIFICATION_PERMISSION = 1002;
    private static final int REQ_EXPORT_BACKUP = 2001;
    private static final int REQ_IMPORT_BACKUP = 2002;
    private static final int MAX_VISIBLE_BANK_SMS = 15;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private ScrollView scrollView;
    private LinearLayout mainContent;
    private LinearLayout smsContent;
    private LinearLayout rulesContent;
    private LinearLayout botSettingsContent;
    private LinearLayout reportsContent;
    private LinearLayout securityContent;
    private LinearLayout topMenuPanel;
    private PopupWindow topMenuPopup;
    private CheckBox enabledBox;
    private EditText webhookInput;
    private EditText secretInput;
    private Spinner themeSpinner;
    private Button editSettingsButton;
    private Button saveSettingsButton;
    private Spinner bankSpinner;
    private CheckBox bankEnabledBox;
    private CheckBox bankCardLast4Box;
    private EditText customBankNameInput;
    private EditText bankSenderInput;
    private EditText bankSampleInput;
    private TextView bankSummaryView;
    private TextView bankEditTitleView;
    private Button deleteBankButton;
    private EditText manualSenderInput;
    private EditText manualBodyInput;
    private TextView connectionStatusView;
    private TextView dashboardStatsView;
    private TextView revenueStatsView;
    private TextView todayMetricView;
    private TextView approvedMetricView;
    private TextView reviewMetricView;
    private TextView conversationsMetricView;
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
    private int pageTopColor;
    private int pageBottomColor;
    private int goldColor;
    private int greenColor;
    private int glassStartColor;
    private int glassEndColor;
    private int softGoldColor;
    private int softGreenColor;
    private boolean editSettingsMode = false;
    private boolean addingCustomBank = false;
    private boolean suppressThemeChange = false;
    private boolean suppressBankSelection = false;
    private boolean appUnlocked = false;
    private boolean lockDialogShowing = false;
    private String selectedConversationKey = "";
    private String bankSmsFilter = "all";
    private SharedPreferences historyPreferences;
    private SharedPreferences incomePreferences;
    private SharedPreferences.OnSharedPreferenceChangeListener historyChangeListener;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        loadPalette();
        buildUi();
        loadSettings();
        registerHistoryAutoRefresh();
        maybeAskAppPassword();
        requestSmsPermission();
        ensureMonitorServiceState();
    }

    @Override
    protected void onResume() {
        super.onResume();
        maybeAskAppPassword();
        refreshHistory();
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (AppLockStore.isEnabled(this) && !isChangingConfigurations()) {
            appUnlocked = false;
        }
    }

    @Override
    protected void onDestroy() {
        if (historyPreferences != null && historyChangeListener != null) {
            historyPreferences.unregisterOnSharedPreferenceChangeListener(historyChangeListener);
        }
        if (incomePreferences != null && historyChangeListener != null) {
            incomePreferences.unregisterOnSharedPreferenceChangeListener(historyChangeListener);
        }
        super.onDestroy();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) {
            return;
        }
        if (requestCode == REQ_EXPORT_BACKUP) {
            writeBackupToUri(data.getData());
        } else if (requestCode == REQ_IMPORT_BACKUP) {
            restoreBackupFromUri(data.getData());
        }
    }

    private void exportBackupFile() {
        try {
            Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            intent.setType("application/json");
            intent.putExtra(Intent.EXTRA_TITLE, BackupStore.suggestedFileName());
            startActivityForResult(intent, REQ_EXPORT_BACKUP);
        } catch (Exception e) {
            Toast.makeText(this, "امکان باز کردن ذخیره‌ساز بکاپ نیست: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void importBackupFile() {
        try {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            intent.setType("application/json");
            startActivityForResult(intent, REQ_IMPORT_BACKUP);
        } catch (Exception e) {
            Toast.makeText(this, "امکان باز کردن فایل بکاپ نیست: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void writeBackupToUri(Uri uri) {
        try (OutputStream output = getContentResolver().openOutputStream(uri)) {
            if (output == null) {
                Toast.makeText(this, "مسیر ذخیره بکاپ باز نشد", Toast.LENGTH_LONG).show();
                return;
            }
            output.write(BackupStore.exportJson(this).getBytes(StandardCharsets.UTF_8));
            output.flush();
            Toast.makeText(this, "بکاپ اپ ذخیره شد ✅", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "خطا در ذخیره بکاپ: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void restoreBackupFromUri(Uri uri) {
        try (InputStream input = getContentResolver().openInputStream(uri)) {
            if (input == null) {
                Toast.makeText(this, "فایل بکاپ باز نشد", Toast.LENGTH_LONG).show();
                return;
            }
            int restored = BackupStore.importJson(this, readAllText(input));
            loadSettings();
            refreshHistory();
            Toast.makeText(this, "بکاپ بازیابی شد ✅ بخش‌های بازیابی‌شده: " + restored, Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "خطا در بازیابی بکاپ: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private String readAllText(InputStream input) throws Exception {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int read;
        while ((read = input.read(chunk)) != -1) {
            buffer.write(chunk, 0, read);
        }
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    private String appLockStatusText() {
        if (AppLockStore.isEnabled(this)) {
            return "✅ قفل ورود فعال است.\nهر بار اپ باز شود، رمز دلخواه شما پرسیده می‌شود.";
        }
        if (AppLockStore.hasPassword(this)) {
            return "🟡 رمز ذخیره شده ولی قفل فعلاً خاموش است.\nمی‌توانی دوباره روشنش کنی یا رمز را تغییر بدهی.";
        }
        return "⚪ قفل ورود خاموش است.\nبرای امنیت اطلاعات کارت، SMS و درآمدها بهتر است رمز فعال باشد.";
    }

    private void maybeAskAppPassword() {
        if (!AppLockStore.isEnabled(this) || appUnlocked || lockDialogShowing) {
            return;
        }
        showUnlockDialog();
    }

    private void showUnlockDialog() {
        lockDialogShowing = true;
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("رمز ورود اپ");
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        input.setPadding(dp(18), dp(10), dp(18), dp(10));

        final AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("🔐 ورود به SellBot SMS Verifier")
                .setMessage("برای مشاهده اطلاعات بانکی، رمز اپ را وارد کن.")
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("ورود", null)
                .setNegativeButton("خروج", null)
                .create();
        dialog.setOnShowListener(d -> {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                if (AppLockStore.verify(MainActivity.this, input.getText().toString())) {
                    appUnlocked = true;
                    lockDialogShowing = false;
                    dialog.dismiss();
                    Toast.makeText(MainActivity.this, "ورود تایید شد ✅", Toast.LENGTH_SHORT).show();
                } else {
                    input.setText("");
                    input.setError("رمز اشتباه است");
                    Toast.makeText(MainActivity.this, "رمز اشتباه است", Toast.LENGTH_SHORT).show();
                }
            });
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(v -> {
                lockDialogShowing = false;
                finish();
            });
        });
        dialog.show();
    }

    private void showSetAppPasswordDialog() {
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(8), 0, dp(8), 0);

        final EditText pass1 = new EditText(this);
        pass1.setHint("رمز جدید، حداقل ۴ کاراکتر");
        pass1.setSingleLine(true);
        pass1.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(pass1, matchWrap());

        final EditText pass2 = new EditText(this);
        pass2.setHint("تکرار رمز");
        pass2.setSingleLine(true);
        pass2.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(pass2, matchWrap());

        final AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("🔑 تنظیم رمز ورود")
                .setMessage("رمز فقط به صورت هش ذخیره می‌شود و خود متن رمز داخل اپ ذخیره نمی‌شود.")
                .setView(box)
                .setPositiveButton("ذخیره و فعال‌سازی", null)
                .setNegativeButton("انصراف", null)
                .create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String first = pass1.getText().toString();
            String second = pass2.getText().toString();
            if (first.trim().length() < 4) {
                pass1.setError("حداقل ۴ کاراکتر وارد کن");
                return;
            }
            if (!first.equals(second)) {
                pass2.setError("تکرار رمز یکی نیست");
                return;
            }
            if (AppLockStore.setPassword(MainActivity.this, first)) {
                appUnlocked = true;
                dialog.dismiss();
                recreate();
                Toast.makeText(MainActivity.this, "قفل ورود فعال شد ✅", Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(MainActivity.this, "رمز معتبر نیست", Toast.LENGTH_LONG).show();
            }
        }));
        dialog.show();
    }

    private void toggleAppLock() {
        if (AppLockStore.isEnabled(this)) {
            confirmDisableAppLock();
            return;
        }
        if (!AppLockStore.hasPassword(this)) {
            showSetAppPasswordDialog();
            return;
        }
        AppLockStore.enable(this);
        appUnlocked = true;
        recreate();
        Toast.makeText(this, "قفل ورود روشن شد ✅", Toast.LENGTH_LONG).show();
    }

    private void confirmDisableAppLock() {
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("برای خاموش کردن، رمز فعلی را وارد کن");
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);

        final AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("🔓 خاموش کردن قفل")
                .setMessage("برای امنیت، اول رمز فعلی را وارد کن.")
                .setView(input)
                .setPositiveButton("خاموش کن", null)
                .setNegativeButton("انصراف", null)
                .create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            if (!AppLockStore.verify(MainActivity.this, input.getText().toString())) {
                input.setText("");
                input.setError("رمز اشتباه است");
                return;
            }
            AppLockStore.disable(MainActivity.this);
            appUnlocked = true;
            dialog.dismiss();
            recreate();
            Toast.makeText(MainActivity.this, "قفل ورود خاموش شد", Toast.LENGTH_LONG).show();
        }));
        dialog.show();
    }

    private void registerHistoryAutoRefresh() {
        historyPreferences = getApplicationContext().getSharedPreferences("sellbot_sms_history", MODE_PRIVATE);
        historyChangeListener = new SharedPreferences.OnSharedPreferenceChangeListener() {
            @Override
            public void onSharedPreferenceChanged(SharedPreferences sharedPreferences, String key) {
                if (!"bank_sms_history".equals(key)
                        && !"history".equals(key)
                        && !"approved_history".equals(key)
                        && !"income_ledger".equals(key)) {
                    return;
                }
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshHistory();
                    }
                });
            }
        };
        historyPreferences.registerOnSharedPreferenceChangeListener(historyChangeListener);
        incomePreferences = getApplicationContext().getSharedPreferences(IncomeStore.PREF_NAME, MODE_PRIVATE);
        incomePreferences.registerOnSharedPreferenceChangeListener(historyChangeListener);
    }

    @Override
    public void onBackPressed() {
        if (topMenuPopup != null && topMenuPopup.isShowing()) {
            topMenuPopup.dismiss();
            return;
        }
        if (smsContent != null && smsContent.getVisibility() == View.VISIBLE) {
            if (!selectedConversationKey.isEmpty()) {
                selectedConversationKey = "";
                renderBankSmsBubbles();
                return;
            }
            showMainScreen();
            return;
        }
        if (isSubPageVisible()) {
            showMainScreen();
            return;
        }
        super.onBackPressed();
    }

    private void buildUi() {
        scrollView = new ScrollView(this);
        scrollView.setBackgroundColor(bgColor);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(14), dp(14), dp(14), dp(22));
        stylePageBackground(root);
        scrollView.addView(root);

        mainContent = new LinearLayout(this);
        mainContent.setOrientation(LinearLayout.VERTICAL);
        root.addView(mainContent, matchWrap());

        smsContent = new LinearLayout(this);
        smsContent.setOrientation(LinearLayout.VERTICAL);
        smsContent.setVisibility(View.GONE);
        root.addView(smsContent, matchWrap());

        rulesContent = addHiddenPage(root);
        botSettingsContent = addHiddenPage(root);
        reportsContent = addHiddenPage(root);
        securityContent = addHiddenPage(root);

        buildMainContent();
        buildSmsContent();
        setContentView(scrollView);
    }

    private LinearLayout addHiddenPage(LinearLayout root) {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setVisibility(View.GONE);
        root.addView(page, matchWrap());
        return page;
    }

    private void buildMainContent() {
        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setPadding(dp(14), dp(14), dp(14), dp(14));
        styleGradientRounded(hero, glassStartColor, glassEndColor, strokeColor, dp(26));
        LinearLayout.LayoutParams heroLp = matchWrap();
        heroLp.setMargins(0, 0, 0, dp(12));
        mainContent.addView(hero, heroLp);

        LinearLayout topRow = new LinearLayout(this);
        topRow.setOrientation(LinearLayout.HORIZONTAL);
        topRow.setGravity(Gravity.CENTER_VERTICAL);
        hero.addView(topRow, matchWrap());

        Button menuButton = new Button(this);
        menuButton.setText("⋮");
        styleButton(menuButton, false);
        topRow.addView(menuButton, new LinearLayout.LayoutParams(dp(46), dp(42)));
        menuButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showTopMenu(v);
            }
        });

        TextView title = new TextView(this);
        title.setText("SellBot SMS Verifier");
        title.setTextSize(21);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(textColor);
        title.setGravity(Gravity.CENTER_VERTICAL);
        topRow.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView liveBadge = new TextView(this);
        liveBadge.setText("LIVE ✅");
        liveBadge.setTextSize(11);
        liveBadge.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        liveBadge.setTextColor(Color.parseColor("#07111F"));
        liveBadge.setGravity(Gravity.CENTER);
        liveBadge.setPadding(dp(10), dp(5), dp(10), dp(5));
        styleRounded(liveBadge, greenColor, greenColor, dp(999));
        topRow.addView(liveBadge, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView desc = new TextView(this);
        desc.setText("داشبورد هوشمند تایید پرداخت؛ SMS بانک را می‌خواند، با ربات هماهنگ می‌کند و نتیجه را شفاف نگه می‌دارد.");
        desc.setTextSize(13);
        desc.setTextColor(mutedColor);
        desc.setGravity(Gravity.RIGHT);
        desc.setLineSpacing(0, 1.15f);
        desc.setPadding(0, dp(12), 0, 0);
        hero.addView(desc, matchWrap());

        topMenuPanel = createTopMenuPanel();
        addSectionTitle(topMenuPanel, "⚡ منوی سریع");
        TextView versionText = new TextView(this);
        versionText.setText("نسخه: " + BuildConfig.VERSION_NAME + "\nتنظیمات فوری و دسترسی سریع");
        versionText.setTextSize(12);
        versionText.setTextColor(mutedColor);
        versionText.setPadding(0, 0, 0, dp(8));
        topMenuPanel.addView(versionText, matchWrap());
        themeSpinner = addSpinner(topMenuPanel, "🎨 تم برنامه", new String[]{"سیستم گوشی", "روشن", "تاریک"});
        themeSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (suppressThemeChange) {
                    return;
                }
                SettingsStore settings = new SettingsStore(MainActivity.this);
                String oldMode = settings.getThemeMode();
                String newMode = selectedThemeMode();
                if (!oldMode.equals(newMode)) {
                    settings.saveThemeMode(newMode);
                    recreate();
                }
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        addButtonRow(topMenuPanel,
                new String[]{"🔎 تراکنش‌ها", "📊 گزارش‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                dismissTopMenu();
                                showSmsScreen();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                dismissTopMenu();
                                showReportsScreen();
                            }
                        }
                });

        LinearLayout dashboard = addDashboardCard(mainContent);
        addSectionTitle(dashboard, "💎 داشبورد تراکنش‌ها");
        dashboardStatsView = new TextView(this);
        dashboardStatsView.setText("وضعیت سیستم در حال آماده‌سازی...");
        dashboardStatsView.setTextSize(12);
        dashboardStatsView.setTextColor(textColor);
        dashboardStatsView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        dashboardStatsView.setPadding(dp(12), dp(9), dp(12), dp(9));
        styleGradientRounded(dashboardStatsView, softGreenColor, inputColor, greenColor, dp(18));
        dashboard.addView(dashboardStatsView, matchWrap());

        revenueStatsView = new TextView(this);
        revenueStatsView.setText("درآمد تاییدشده در حال محاسبه...");
        revenueStatsView.setTextSize(12);
        revenueStatsView.setTextColor(textColor);
        revenueStatsView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        revenueStatsView.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        revenueStatsView.setLineSpacing(dp(2), 1.12f);
        revenueStatsView.setPadding(dp(12), dp(10), dp(12), dp(10));
        styleGradientRounded(revenueStatsView, softGoldColor, inputColor, goldColor, dp(18));
        LinearLayout.LayoutParams revenueLp = matchWrap();
        revenueLp.setMargins(0, dp(8), 0, 0);
        dashboard.addView(revenueStatsView, revenueLp);

        LinearLayout metricRowOne = new LinearLayout(this);
        metricRowOne.setOrientation(LinearLayout.HORIZONTAL);
        metricRowOne.setPadding(0, dp(8), 0, dp(2));
        dashboard.addView(metricRowOne, matchWrap());
        todayMetricView = addMetricCard(metricRowOne, "📨", "پیامک امروز", softGoldColor, goldColor, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openSmsFilter("today");
            }
        });
        approvedMetricView = addMetricCard(metricRowOne, "✅", "تایید شده", softGreenColor, greenColor, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openSmsFilter("approved");
            }
        });

        LinearLayout metricRowTwo = new LinearLayout(this);
        metricRowTwo.setOrientation(LinearLayout.HORIZONTAL);
        metricRowTwo.setPadding(0, dp(2), 0, dp(8));
        dashboard.addView(metricRowTwo, matchWrap());
        reviewMetricView = addMetricCard(metricRowTwo, "⚠️", "نیاز بررسی", neutralColor, goldColor, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openSmsFilter("review");
            }
        });
        conversationsMetricView = addMetricCard(metricRowTwo, "🏦", "سرشماره‌ها", inputColor, strokeColor, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openSmsFilter("all");
            }
        });

        addButtonRow(dashboard,
                new String[]{"🔎 بررسی تراکنش‌ها", "🏦 بانک‌ها و الگوها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showSmsScreen();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showRulesScreen();
                            }
                        }
                });
        addButtonRow(dashboard,
                new String[]{"🤖 تنظیمات تلگرام", "📊 گزارش‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showBotSettingsScreen();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showReportsScreen();
                            }
                        }
                });
        addButtonRow(dashboard,
                new String[]{"🔐 امنیت", "🧪 تست اتصال"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showSecurityScreen();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                saveSettings(false);
                                sendTestWebhook();
                            }
                        }
                });

        connectionStatusView = new TextView(this);
        connectionStatusView.setText("وضعیت اتصال هنوز تست نشده است.");
        connectionStatusView.setTextSize(12);
        connectionStatusView.setTextColor(mutedColor);
        connectionStatusView.setGravity(Gravity.CENTER);
        connectionStatusView.setPadding(dp(10), dp(8), dp(10), dp(8));
        styleRounded(connectionStatusView, inputColor, strokeColor, dp(12));
        dashboard.addView(connectionStatusView, matchWrap());

        addPageHeader(botSettingsContent, "🤖 تنظیمات تلگرام", "اتصال امن اپ به Webhook ربات و کلید Secret اینجا مدیریت می‌شود.");
        addPageHeader(rulesContent, "🏦 بانک‌ها و الگوهای SMS", "بانک‌ها، سرشماره‌ها و نمونه SMS هر بانک را اینجا تعریف کن.");
        addPageHeader(reportsContent, "📊 گزارش‌ها", "لاگ فنی اتصال اپ، تست‌ها و خطاهای ارتباط با ربات.");
        addPageHeader(securityContent, "🔐 امنیت", "چک‌لیست امنیت اتصال، جلوگیری از تکرار و نکته‌های حساس.");

        LinearLayout connectionCard = addCard(botSettingsContent);
        addSectionTitle(connectionCard, "⚙️ تنظیمات اصلی");

        editSettingsButton = new Button(this);
        editSettingsButton.setText("🔒 تنظیمات قفل است؛ برای تغییر بزن");
        styleButton(editSettingsButton, false);
        connectionCard.addView(editSettingsButton, matchWrap());
        editSettingsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                editSettingsMode = !editSettingsMode;
                applySettingsEditMode();
            }
        });

        enabledBox = new CheckBox(this);
        enabledBox.setText("پردازش خودکار SMS فعال باشد");
        styleCheckBox(enabledBox);
        connectionCard.addView(enabledBox, matchWrap());

        webhookInput = addInput(connectionCard, "Webhook URL ربات", "https://example.com/payment/sms-webhook", false, 1);
        secretInput = addInput(connectionCard, "Secret Key اتصال", "کلید امنیتی مشترک با ربات", true, 1);

        saveSettingsButton = new Button(this);
        saveSettingsButton.setText("💾 ذخیره تنظیمات اصلی");
        styleButton(saveSettingsButton, true);
        connectionCard.addView(saveSettingsButton, matchWrap());
        saveSettingsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings(true);
                editSettingsMode = false;
                applySettingsEditMode();
            }
        });

        LinearLayout bankCard = addCard(rulesContent);
        addSectionTitle(bankCard, "🏦 بانک‌ها و الگوهای SMS");
        TextView bankHelp = new TextView(this);
        bankHelp.setText("بانک را انتخاب کن یا با دکمه + بانک جدید بساز؛ هر بانک چند سرشماره جدا و نمونه SMS خودش را دارد.");
        bankHelp.setTextSize(12);
        bankHelp.setTextColor(mutedColor);
        bankHelp.setPadding(0, 0, 0, dp(8));
        bankCard.addView(bankHelp, matchWrap());

        bankEditTitleView = new TextView(this);
        bankEditTitleView.setTextSize(12);
        bankEditTitleView.setTextColor(mutedColor);
        bankEditTitleView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        bankEditTitleView.setPadding(0, dp(2), 0, dp(4));
        bankCard.addView(bankEditTitleView, matchWrap());

        bankSpinner = addSpinner(bankCard, "انتخاب/ویرایش بانک", new SettingsStore(this).getBankNames());
        bankSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (suppressBankSelection) {
                    return;
                }
                addingCustomBank = false;
                loadSelectedBank();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        customBankNameInput = addInput(bankCard, "نام بانک جدید", new SettingsStore(this).getCustomBankNameHint(), false, 1);

        bankEnabledBox = new CheckBox(this);
        bankEnabledBox.setText("این بانک فعال باشد");
        styleCheckBox(bankEnabledBox);
        bankCard.addView(bankEnabledBox, matchWrap());

        bankCardLast4Box = new CheckBox(this);
        bankCardLast4Box.setText("اگر SMS همین بانک چهار رقم کارت مبدا داشت، برای تطبیق دقیق ارسال شود");
        styleCheckBox(bankCardLast4Box);
        bankCard.addView(bankCardLast4Box, matchWrap());

        bankSenderInput = addInput(bankCard, "سرشماره‌های SMS همین بانک", "مثال: 20004861، 3000...\nهر خط یا کاما یک سرشماره", false, 3);
        bankSampleInput = addInput(bankCard, "نمونه SMS همین بانک", "نمونه پیامک واریز همین بانک را کامل اینجا paste کن", false, 8);

        LinearLayout bankActionRow = new LinearLayout(this);
        bankActionRow.setOrientation(LinearLayout.HORIZONTAL);
        bankActionRow.setPadding(0, dp(4), 0, dp(2));
        bankCard.addView(bankActionRow, matchWrap());

        Button saveBankButton = new Button(this);
        saveBankButton.setText("✅ ذخیره بانک");
        styleButton(saveBankButton, true);
        bankActionRow.addView(saveBankButton, weightedButtonLp());
        saveBankButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSelectedBank();
            }
        });

        deleteBankButton = new Button(this);
        deleteBankButton.setText("🗑 حذف/غیرفعال");
        styleButton(deleteBankButton, false);
        bankActionRow.addView(deleteBankButton, weightedButtonLp());
        deleteBankButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                deleteSelectedBank();
            }
        });

        Button addBankButton = new Button(this);
        addBankButton.setText("➕ ثبت بانک جدید با همین فرم");
        styleButton(addBankButton, false);
        bankCard.addView(addBankButton, matchWrap());
        addBankButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startAddCustomBank();
            }
        });

        bankSummaryView = new TextView(this);
        bankSummaryView.setTextSize(12);
        bankSummaryView.setTextIsSelectable(true);
        bankSummaryView.setTextColor(textColor);
        bankSummaryView.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleRounded(bankSummaryView, inputColor, strokeColor, dp(12));
        bankCard.addView(bankSummaryView, matchWrap());

        LinearLayout securityCard = addCard(securityContent);
        addSectionTitle(securityCard, "🛡️ وضعیت امنیت");
        TextView securityText = new TextView(this);
        securityText.setText("✅ Secret Key روی درخواست‌ها بررسی می‌شود.\n✅ پیامک‌های تکراری دوباره تایید نمی‌شوند.\n✅ Webhook فقط با آدرس HTTPS پیشنهاد می‌شود.\n✅ Secret داخل اپ مخفی نمایش داده می‌شود.\n\nبرای تغییر Webhook یا Secret وارد «تنظیمات تلگرام» شو.");
        securityText.setTextColor(textColor);
        securityText.setTextSize(13);
        securityText.setLineSpacing(0, 1.18f);
        securityText.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleGradientRounded(securityText, softGreenColor, inputColor, greenColor, dp(18));
        securityCard.addView(securityText, matchWrap());

        Button batteryButton = new Button(this);
        batteryButton.setText("🔋 باز کردن تنظیمات مصرف باتری");
        styleButton(batteryButton, true);
        securityCard.addView(batteryButton, matchWrap());
        batteryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openBatteryOptimizationSettings();
            }
        });

        addButtonRow(securityCard,
                new String[]{"🤖 تنظیمات تلگرام", "📊 گزارش‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showBotSettingsScreen();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showReportsScreen();
                            }
                        }
                });

        LinearLayout lockCard = addCard(securityContent);
        addSectionTitle(lockCard, "🔐 قفل ورود به اپ");
        TextView lockHelp = new TextView(this);
        lockHelp.setText(appLockStatusText());
        lockHelp.setTextColor(textColor);
        lockHelp.setTextSize(13);
        lockHelp.setLineSpacing(0, 1.16f);
        lockHelp.setPadding(dp(10), dp(10), dp(10), dp(10));
        styleGradientRounded(lockHelp, softGoldColor, inputColor, goldColor, dp(18));
        lockCard.addView(lockHelp, matchWrap());
        addButtonRow(lockCard,
                new String[]{"🔑 تنظیم/تغییر رمز", AppLockStore.isEnabled(this) ? "🔓 خاموش کردن قفل" : "🔒 روشن کردن قفل"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showSetAppPasswordDialog();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                toggleAppLock();
                            }
                        }
                });

        LinearLayout backupCard = addCard(securityContent);
        addSectionTitle(backupCard, "📦 بکاپ اپ");
        TextView backupHelp = new TextView(this);
        backupHelp.setText("بکاپ شامل تنظیمات تلگرام، بانک‌ها، پیامک‌های بانکی، دفتر درآمد و تنظیمات قفل است. فایل را جای امن نگه دار؛ Secret هم داخل بکاپ ذخیره می‌شود.");
        backupHelp.setTextColor(mutedColor);
        backupHelp.setTextSize(12);
        backupHelp.setLineSpacing(0, 1.15f);
        backupHelp.setPadding(0, 0, 0, dp(8));
        backupCard.addView(backupHelp, matchWrap());
        addButtonRow(backupCard,
                new String[]{"📥 بازیابی بکاپ", "📤 ذخیره بکاپ"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                importBackupFile();
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                exportBackupFile();
                            }
                        }
                });

        LinearLayout logCard = addCard(reportsContent);
        addSectionTitle(logCard, "📋 لاگ کامل برنامه");
        TextView logHelp = new TextView(this);
        logHelp.setText("اینجا فقط لاگ فنی و خطاهاست؛ پیامک‌های بانکی از دکمه بالای صفحه باز می‌شوند.");
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
        logRow.addView(refreshButton, weightedButtonLp());
        refreshButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshHistory();
            }
        });

        Button clearButton = new Button(this);
        clearButton.setText("پاک کردن لاگ");
        styleButton(clearButton, false);
        logRow.addView(clearButton, weightedButtonLp());
        clearButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                HistoryStore.clearTechnicalLogs(MainActivity.this);
                refreshHistory();
                Toast.makeText(MainActivity.this, "فقط لاگ فنی پاک شد؛ پیامک‌های بانکی محفوظ ماند", Toast.LENGTH_LONG).show();
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
    }

    private void buildSmsContent() {
        LinearLayout smsCard = addDashboardCard(smsContent);
        TextView title = new TextView(this);
        title.setText("🔎 بررسی تراکنش‌ها");
        title.setTextSize(21);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(textColor);
        title.setGravity(Gravity.CENTER);
        smsCard.addView(title, matchWrap());

        TextView hint = new TextView(this);
        hint.setText("اینجا صفحه جداگانه بررسی تراکنش‌هاست؛ هر سرشماره مثل یک گفت‌وگوی بانکی جدا نمایش داده می‌شود.");
        hint.setTextColor(mutedColor);
        hint.setTextSize(13);
        hint.setGravity(Gravity.CENTER);
        hint.setLineSpacing(0, 1.15f);
        hint.setPadding(0, dp(6), 0, dp(10));
        smsCard.addView(hint, matchWrap());

        addButtonRow(smsCard,
                new String[]{"⬅️ داشبورد", "🔎 بررسی پیامک‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showMainScreen();
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

        Button refreshSmsButton = new Button(this);
        refreshSmsButton.setText("🔄 همگام‌سازی با ربات و صندوق");
        styleButton(refreshSmsButton, false);
        smsCard.addView(refreshSmsButton, matchWrap());
        refreshSmsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveSettings(false);
                scanInboxNow();
            }
        });

        addButtonRow(smsCard,
                new String[]{"📨 امروز", "همه"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                setBankSmsFilter("today");
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                setBankSmsFilter("all");
                            }
                        }
                });
        addButtonRow(smsCard,
                new String[]{"✅ تایید شده", "⚠️ نیاز بررسی"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                setBankSmsFilter("approved");
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                setBankSmsFilter("review");
                            }
                        }
                });
        addButtonRow(smsCard,
                new String[]{"🔁 تکراری", "🏦 تنظیم بانک‌ها"},
                new View.OnClickListener[]{
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                setBankSmsFilter("duplicate");
                            }
                        },
                        new View.OnClickListener() {
                            @Override
                            public void onClick(View v) {
                                showRulesScreen();
                            }
                        }
                });

        bankSmsListView = new LinearLayout(this);
        bankSmsListView.setOrientation(LinearLayout.VERTICAL);
        smsCard.addView(bankSmsListView, matchWrap());
    }

    private void loadSettings() {
        SettingsStore settings = new SettingsStore(this);
        enabledBox.setChecked(settings.isEnabled());
        webhookInput.setText(settings.getWebhookUrl());
        secretInput.setText(settings.getSecret());
        suppressThemeChange = true;
        themeSpinner.setSelection(themeIndex(settings.getThemeMode()));
        suppressThemeChange = false;
        rebuildBankSpinner(0);
        refreshBankSummary();
        editSettingsMode = false;
        applySettingsEditMode();
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
                "",
                false
        );
        settings.saveThemeMode(newTheme);
        ensureMonitorServiceState();
        if (showToast) {
            Toast.makeText(this, "تنظیمات ذخیره شد", Toast.LENGTH_SHORT).show();
        }
        if (!oldTheme.equals(newTheme)) {
            recreate();
        }
    }

    private void applySettingsEditMode() {
        enabledBox.setEnabled(editSettingsMode);
        webhookInput.setEnabled(editSettingsMode);
        secretInput.setEnabled(editSettingsMode);
        saveSettingsButton.setEnabled(editSettingsMode);
        saveSettingsButton.setAlpha(editSettingsMode ? 1f : 0.45f);
        editSettingsButton.setText(editSettingsMode
                ? "✏️ حالت ویرایش فعال است؛ بعد از تغییر ذخیره کن"
                : "🔒 تنظیمات قفل است؛ برای تغییر بزن");
    }

    private void rebuildBankSpinner(int selectedIndex) {
        if (bankSpinner == null) {
            return;
        }
        SettingsStore settings = new SettingsStore(this);
        suppressBankSelection = true;
        ArrayAdapter<String> adapter = new ArrayAdapter<>(
                this,
                android.R.layout.simple_spinner_item,
                settings.getBankNames()
        );
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        bankSpinner.setAdapter(adapter);
        int safeIndex = Math.max(0, Math.min(selectedIndex, settings.getBankCount() - 1));
        bankSpinner.setSelection(safeIndex);
        suppressBankSelection = false;
        loadSelectedBank();
    }

    private void loadSelectedBank() {
        if (addingCustomBank
                || bankSpinner == null
                || bankEditTitleView == null
                || customBankNameInput == null
                || bankEnabledBox == null
                || bankCardLast4Box == null
                || bankSenderInput == null
                || bankSampleInput == null) {
            return;
        }
        SettingsStore settings = new SettingsStore(this);
        SettingsStore.BankConfig bank = settings.getBank(bankSpinner.getSelectedItemPosition());
        bankEditTitleView.setText(settings.getBankTitleForEdit(bankSpinner.getSelectedItemPosition()));
        customBankNameInput.setVisibility(bank.custom ? View.VISIBLE : View.GONE);
        customBankNameInput.setText(bank.custom ? bank.name : "");
        bankEnabledBox.setChecked(bank.enabled);
        bankCardLast4Box.setChecked(bank.cardLast4Enabled);
        bankSenderInput.setText(bank.senderFilters);
        bankSampleInput.setText(bank.sampleSms);
        if (deleteBankButton != null) {
            deleteBankButton.setVisibility(View.VISIBLE);
            deleteBankButton.setText(bank.custom ? "🗑 حذف بانک" : "🧹 پاک‌کردن الگو");
        }
    }

    private void startAddCustomBank() {
        addingCustomBank = true;
        bankEditTitleView.setText("➕ افزودن بانک جدید");
        customBankNameInput.setVisibility(View.VISIBLE);
        customBankNameInput.setText("");
        bankEnabledBox.setChecked(true);
        bankCardLast4Box.setChecked(false);
        bankSenderInput.setText("");
        bankSampleInput.setText("");
        if (deleteBankButton != null) {
            deleteBankButton.setVisibility(View.GONE);
        }
        customBankNameInput.requestFocus();
        Toast.makeText(this, "نام بانک، سرشماره و نمونه SMS را وارد کن", Toast.LENGTH_LONG).show();
    }

    private void saveSelectedBank() {
        boolean bankEnabled = bankEnabledBox.isChecked();
        boolean cardLast4Enabled = bankCardLast4Box.isChecked();
        String name = text(customBankNameInput);
        String senders = text(bankSenderInput);
        String sample = text(bankSampleInput);
        if (addingCustomBank && name.isEmpty()) {
            Toast.makeText(this, "نام بانک جدید را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }
        if (bankEnabled && senders.isEmpty()) {
            Toast.makeText(this, "برای فعال کردن بانک، حداقل یک سرشماره SMS وارد کن", Toast.LENGTH_LONG).show();
            return;
        }
        if (bankEnabled && sample.isEmpty()) {
            Toast.makeText(this, "برای بار اول، یک نمونه SMS واقعی همین بانک را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }

        SettingsStore settings = new SettingsStore(this);
        int selectedIndex;
        if (addingCustomBank) {
            selectedIndex = settings.addCustomBank(name, bankEnabled, senders, sample, cardLast4Enabled);
            if (selectedIndex < 0) {
                Toast.makeText(this, "فعلاً حداکثر ۵ بانک سفارشی قابل ثبت است", Toast.LENGTH_LONG).show();
                return;
            }
            addingCustomBank = false;
            rebuildBankSpinner(selectedIndex);
        } else {
            selectedIndex = bankSpinner.getSelectedItemPosition();
            settings.saveBank(selectedIndex, name, bankEnabled, senders, sample, cardLast4Enabled);
            loadSelectedBank();
        }
        refreshBankSummary();
        Toast.makeText(this, "تنظیمات بانک ذخیره شد", Toast.LENGTH_SHORT).show();
    }

    private void deleteSelectedBank() {
        if (addingCustomBank || bankSpinner == null) {
            startAddCustomBank();
            return;
        }
        SettingsStore settings = new SettingsStore(this);
        int index = bankSpinner.getSelectedItemPosition();
        SettingsStore.BankConfig bank = settings.getBank(index);
        settings.deleteOrResetBank(index);
        addingCustomBank = false;
        rebuildBankSpinner(Math.max(0, Math.min(index, settings.getBankCount() - 1)));
        refreshBankSummary();
        Toast.makeText(this, bank.custom ? "بانک حذف شد" : "بانک آماده غیرفعال و الگوی آن پاک شد", Toast.LENGTH_LONG).show();
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
            showConnectionStatus(false, "Webhook URL و Secret را وارد کن.");
            Toast.makeText(this, "Webhook URL و Secret را وارد کن", Toast.LENGTH_LONG).show();
            return;
        }

        showConnectionStatus(false, "در حال تست اتصال به ربات...");
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
                        if (result.ok) {
                            showConnectionStatus(true, "✅ اتصال با موفقیت انجام شد. Webhook و Secret درست هستند.");
                            Toast.makeText(MainActivity.this, "اتصال با موفقیت انجام شد", Toast.LENGTH_LONG).show();
                        } else {
                            showConnectionStatus(false, "❌ اتصال برقرار نشد: " + WebhookClient.friendlyError(result));
                            Toast.makeText(MainActivity.this, "اتصال ناموفق بود", Toast.LENGTH_LONG).show();
                        }
                    }
                });
            }
        });
    }

    private void showConnectionStatus(boolean ok, String text) {
        if (connectionStatusView == null) {
            return;
        }
        connectionStatusView.setText(text);
        connectionStatusView.setTextColor(ok ? Color.parseColor("#16A34A") : mutedColor);
        styleRounded(connectionStatusView, ok ? approvedColor : inputColor, ok ? Color.parseColor("#22C55E") : strokeColor, dp(12));
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

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    REQ_NOTIFICATION_PERMISSION
            );
        }
    }

    private void ensureMonitorServiceState() {
        SettingsStore settings = new SettingsStore(this);
        if (settings.isEnabled()) {
            requestNotificationPermissionIfNeeded();
            SmsMonitorService.start(this);
        } else {
            SmsMonitorService.stop(this);
        }
    }

    private void openBatteryOptimizationSettings() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PowerManager powerManager = (PowerManager) getSystemService(POWER_SERVICE);
                if (powerManager != null && !powerManager.isIgnoringBatteryOptimizations(getPackageName())) {
                    Intent requestIntent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    requestIntent.setData(Uri.parse("package:" + getPackageName()));
                    startActivity(requestIntent);
                    Toast.makeText(this, "برای کارکرد پایدار، اجازه فعالیت پس‌زمینه را تایید کن", Toast.LENGTH_LONG).show();
                    return;
                }
            }
            Intent detailsIntent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
            detailsIntent.setData(Uri.parse("package:" + getPackageName()));
            startActivity(detailsIntent);
            Toast.makeText(this, "در Battery، حالت Unrestricted/بدون محدودیت را انتخاب کن", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            try {
                startActivity(new Intent(Settings.ACTION_SETTINGS));
            } catch (Exception ignored) {
                Toast.makeText(this, "تنظیمات گوشی باز نشد؛ دستی وارد Battery app settings شو", Toast.LENGTH_LONG).show();
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
                final int retried = retryPendingStoredSmsEntries();
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshHistory();
                        showSmsScreen();
                        Toast.makeText(
                                MainActivity.this,
                                "بررسی تمام شد: " + scanned + " پیامک، " + retried + " استعلام دوباره",
                                Toast.LENGTH_LONG
                        ).show();
                    }
                });
            }
        });
    }

    private int retryPendingStoredSmsEntries() {
        HistoryStore.Entry[] entries = HistoryStore.getBankSmsEntries(this);
        if (entries == null || entries.length == 0) {
            return 0;
        }
        int retried = 0;
        Set<String> seen = new HashSet<>();
        for (HistoryStore.Entry entry : entries) {
            if (entry == null || isConfirmedPaymentEntry(entry) || isDuplicateApprovedEntry(entry)) {
                continue;
            }
            String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
            if (rawSms.isEmpty()) {
                rawSms = extractDetailBlock(entry.detail, "متن SMS:");
            }
            String sender = entrySender(entry);
            if (sender.trim().isEmpty() || rawSms.trim().isEmpty() || !PaymentSmsParser.isIncomingPayment(rawSms)) {
                continue;
            }
            long amount = PaymentSmsParser.extractAmount(rawSms);
            String reference = PaymentSmsParser.extractReference(rawSms);
            String eventId = extractDetailLine(entry.detail, "🔐 شناسه داخلی:");
            if (eventId.isEmpty()) {
                eventId = PaymentSmsParser.buildEventId(sender, rawSms, amount, reference);
            }
            if (eventId.isEmpty() || seen.contains(eventId) || !HistoryStore.shouldRetryUnique(this, eventId)) {
                continue;
            }
            seen.add(eventId);
            SmsProcessor.handleIncomingSms(this, sender, rawSms, parseEntryTimeMillis(entry.time));
            retried++;
            if (retried >= 10) {
                break;
            }
        }
        return retried;
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
                        showSmsScreen();
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
        renderDashboardStats();
        renderBankSmsBubbles();
        String history = HistoryStore.get(this);
        historyView.setText(history == null || history.trim().isEmpty()
                ? "هنوز لاگی ثبت نشده است."
                : makeLogReadable(history));
    }

    private void renderDashboardStats() {
        if (dashboardStatsView == null
                || todayMetricView == null
                || approvedMetricView == null
                || reviewMetricView == null
                || conversationsMetricView == null) {
            return;
        }
        HistoryStore.Entry[] entries = HistoryStore.getBankSmsEntries(this);
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
        SettingsStore settings = new SettingsStore(this);
        int todayCount = 0;
        int approved = 0;
        int review = 0;
        int conversations = buildConversationSummaries(filterBankSmsEntries(entries)).size();
        for (HistoryStore.Entry entry : entries) {
            if (isSystemSmsEntry(entry) || !shouldDisplayBankEntry(entry, settings)) {
                continue;
            }
            if (entry.time != null && entry.time.startsWith(today)) {
                todayCount++;
            }
            if (isConfirmedPaymentEntry(entry)) {
                approved++;
            } else if (entry.rejected) {
                review++;
            }
        }
        dashboardStatsView.setText("وضعیت ربات: متصل و آماده ✅   |   آخرین بروزرسانی: " + new SimpleDateFormat("HH:mm", Locale.US).format(new Date()));
        todayMetricView.setText(String.valueOf(todayCount));
        approvedMetricView.setText(String.valueOf(approved));
        reviewMetricView.setText(String.valueOf(review));
        conversationsMetricView.setText(String.valueOf(conversations));
        renderRevenueStats(entries, settings);
    }

    private void renderRevenueStats(HistoryStore.Entry[] entries, SettingsStore settings) {
        if (revenueStatsView == null) {
            return;
        }
        syncIncomeLedgerFromHistory(entries, settings);
        IncomeStore.Stats stats = IncomeStore.getStats(this);
        revenueStatsView.setText("💰 درآمد تاییدشده"
                + "\n▫️ امروز: " + formatToman(stats.todayTotal)
                + "\n▫️ ۷ روز اخیر: " + formatToman(stats.weekTotal)
                + "\n▫️ ماه جاری: " + formatToman(stats.monthTotal)
                + "\n▫️ تعداد تاییدها: " + stats.approvedCount);
    }

    private void syncIncomeLedgerFromHistory(HistoryStore.Entry[] entries, SettingsStore settings) {
        if (entries == null || entries.length == 0) {
            return;
        }
        for (HistoryStore.Entry entry : entries) {
            if (!isConfirmedPaymentEntry(entry)
                    || isSystemSmsEntry(entry)
                    || !shouldDisplayBankEntry(entry, settings)) {
                continue;
            }
            long amount = extractEntryTomanAmount(entry);
            if (amount <= 0) {
                continue;
            }
            String eventId = extractDetailLine(entry.detail, "🔐 شناسه داخلی:");
            if (eventId.isEmpty()) {
                eventId = entryFingerprint(entry);
            }
            IncomeStore.record(
                    this,
                    eventId,
                    amount,
                    isManualApprovedEntry(entry) ? "تایید دستی داخل اپ" : "تایید خودکار SMS",
                    entryBank(entry),
                    entrySender(entry),
                    extractDetailLine(entry.detail, "🔖 پیگیری:"),
                    parseEntryTimeMillis(entry.time)
            );
        }
    }

    private long extractEntryTomanAmount(HistoryStore.Entry entry) {
        if (entry == null) {
            return 0;
        }
        String amount = extractDetailLine(entry.detail, "💵 معادل تقریبی:");
        if (!amount.isEmpty()) {
            return parseMoneyAmount(amount, false);
        }
        amount = extractDetailLine(entry.detail, "💰 مبلغ ربات:");
        if (!amount.isEmpty()) {
            return parseMoneyAmount(amount, false);
        }
        amount = extractDetailLine(entry.detail, "💰 مبلغ SMS:");
        if (!amount.isEmpty()) {
            boolean rial = amount.toLowerCase(Locale.US).contains("rial")
                    || amount.toLowerCase(Locale.US).contains("irr")
                    || amount.contains("ریال");
            return parseMoneyAmount(amount, rial);
        }
        return 0;
    }

    private long parseMoneyAmount(String text, boolean rawIsRial) {
        String normalized = PaymentSmsParser.normalizeDigits(text == null ? "" : text);
        String digits = normalized.replaceAll("[^0-9]", "");
        if (digits.isEmpty()) {
            return 0;
        }
        try {
            long value = Long.parseLong(digits);
            return rawIsRial ? Math.round(value / 10.0) : value;
        } catch (Exception ignored) {
            return 0;
        }
    }

    private long parseEntryTimeMillis(String time) {
        if (time == null || time.trim().length() < 10) {
            return System.currentTimeMillis();
        }
        try {
            Date date = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(time.trim());
            return date == null ? System.currentTimeMillis() : date.getTime();
        } catch (Exception ignored) {
            return System.currentTimeMillis();
        }
    }

    private boolean isEntryInLastDays(String time, int days) {
        if (time == null || time.trim().length() < 10) {
            return false;
        }
        try {
            Date date = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(time.trim());
            if (date == null) {
                return false;
            }
            Calendar from = Calendar.getInstance();
            from.add(Calendar.DAY_OF_YEAR, -Math.max(1, days) + 1);
            from.set(Calendar.HOUR_OF_DAY, 0);
            from.set(Calendar.MINUTE, 0);
            from.set(Calendar.SECOND, 0);
            from.set(Calendar.MILLISECOND, 0);
            return !date.before(from.getTime());
        } catch (Exception ignored) {
            return false;
        }
    }

    private String formatToman(long amount) {
        return String.format(Locale.US, "%,d تومان", Math.max(0, amount));
    }

    private void showSmsScreen() {
        showOnly(smsContent);
        refreshHistory();
        scrollView.post(new Runnable() {
            @Override
            public void run() {
                scrollView.smoothScrollTo(0, 0);
            }
        });
    }

    private void openSmsFilter(String filter) {
        bankSmsFilter = filter == null || filter.trim().isEmpty() ? "all" : filter.trim();
        selectedConversationKey = "";
        showSmsScreen();
    }

    private void showMainScreen() {
        showOnly(mainContent);
        scrollToTop();
    }

    private void showRulesScreen() {
        showOnly(rulesContent);
        scrollToTop();
    }

    private void showBotSettingsScreen() {
        showOnly(botSettingsContent);
        scrollToTop();
    }

    private void showReportsScreen() {
        showOnly(reportsContent);
        refreshHistory();
        scrollToTop();
    }

    private void showSecurityScreen() {
        showOnly(securityContent);
        scrollToTop();
    }

    private void showOnly(LinearLayout target) {
        mainContent.setVisibility(target == mainContent ? View.VISIBLE : View.GONE);
        smsContent.setVisibility(target == smsContent ? View.VISIBLE : View.GONE);
        rulesContent.setVisibility(target == rulesContent ? View.VISIBLE : View.GONE);
        botSettingsContent.setVisibility(target == botSettingsContent ? View.VISIBLE : View.GONE);
        reportsContent.setVisibility(target == reportsContent ? View.VISIBLE : View.GONE);
        securityContent.setVisibility(target == securityContent ? View.VISIBLE : View.GONE);
    }

    private boolean isSubPageVisible() {
        return (rulesContent != null && rulesContent.getVisibility() == View.VISIBLE)
                || (botSettingsContent != null && botSettingsContent.getVisibility() == View.VISIBLE)
                || (reportsContent != null && reportsContent.getVisibility() == View.VISIBLE)
                || (securityContent != null && securityContent.getVisibility() == View.VISIBLE);
    }

    private void scrollToTop() {
        scrollView.post(new Runnable() {
            @Override
            public void run() {
                scrollView.smoothScrollTo(0, 0);
            }
        });
    }

    private void renderBankSmsBubbles() {
        if (bankSmsListView == null) {
            return;
        }
        bankSmsListView.removeAllViews();
        HistoryStore.Entry[] allEntries = HistoryStore.getBankSmsEntries(this);
        HistoryStore.Entry[] entries = filterBankSmsEntries(allEntries);
        if (entries.length == 0) {
            TextView empty = new TextView(this);
            empty.setText("در این فیلتر هنوز تراکنشی ثبت نشده است.\nفیلتر فعلی: " + bankSmsFilterTitle());
            empty.setTextColor(mutedColor);
            empty.setTextSize(12);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(dp(10), dp(20), dp(10), dp(20));
            bankSmsListView.addView(empty, matchWrap());
            return;
        }

        Map<String, ConversationSummary> conversations = buildConversationSummaries(entries);
        if (!selectedConversationKey.isEmpty()) {
            ConversationSummary selected = conversations.get(selectedConversationKey);
            if (selected == null) {
                selectedConversationKey = "";
            } else {
                renderConversationThread(selected, entries);
                return;
            }
        }

        TextView header = new TextView(this);
        header.setText("فیلتر: " + bankSmsFilterTitle() + "  •  " + entries.length + " تراکنش  •  " + conversations.size() + " مکالمه");
        header.setTextSize(13);
        header.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        header.setTextColor(goldColor);
        header.setGravity(Gravity.CENTER);
        header.setPadding(dp(10), dp(8), dp(10), dp(8));
        styleGradientRounded(header, softGoldColor, inputColor, goldColor, dp(18));
        bankSmsListView.addView(header, matchWrap());

        for (ConversationSummary summary : conversations.values()) {
            addConversationRow(bankSmsListView, summary);
        }
    }

    private void setBankSmsFilter(String filter) {
        bankSmsFilter = filter == null || filter.trim().isEmpty() ? "all" : filter.trim();
        selectedConversationKey = "";
        renderBankSmsBubbles();
        Toast.makeText(this, "فیلتر: " + bankSmsFilterTitle(), Toast.LENGTH_SHORT).show();
    }

    private HistoryStore.Entry[] filterBankSmsEntries(HistoryStore.Entry[] entries) {
        if (entries == null || entries.length == 0) {
            return new HistoryStore.Entry[0];
        }
        List<HistoryStore.Entry> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        SettingsStore settings = new SettingsStore(this);
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
        for (HistoryStore.Entry entry : entries) {
            if (isSystemSmsEntry(entry)) {
                continue;
            }
            if (!shouldDisplayBankEntry(entry, settings)) {
                continue;
            }
            String fingerprint = entryFingerprint(entry);
            if (!fingerprint.isEmpty() && seen.contains(fingerprint)) {
                continue;
            }
            String title = entry.title == null ? "" : entry.title;
            String detail = entry.detail == null ? "" : entry.detail;
            String combined = title + "\n" + detail;
            if ("all".equals(bankSmsFilter)) {
                out.add(entry);
            } else if ("today".equals(bankSmsFilter) && entry.time != null && entry.time.startsWith(today)) {
                out.add(entry);
            } else if ("approved".equals(bankSmsFilter) && isConfirmedPaymentEntry(entry)) {
                out.add(entry);
            } else if ("duplicate".equals(bankSmsFilter) && (combined.contains("قبلاً") || combined.toLowerCase(Locale.US).contains("duplicate"))) {
                out.add(entry);
            } else if ("review".equals(bankSmsFilter) && (entry.rejected || combined.contains("پیدا نشد") || combined.contains("نیاز") || combined.contains("چند پرداخت") || combined.contains("قبلاً برای پرداخت") || isDuplicateApprovedEntry(entry))) {
                out.add(entry);
            }
            if (!fingerprint.isEmpty()) {
                seen.add(fingerprint);
            }
            if (out.size() >= MAX_VISIBLE_BANK_SMS) {
                break;
            }
        }
        return out.toArray(new HistoryStore.Entry[0]);
    }

    private String bankSmsFilterTitle() {
        if ("today".equals(bankSmsFilter)) {
            return "پیامک‌های امروز";
        }
        if ("approved".equals(bankSmsFilter)) {
            return "تایید شده";
        }
        if ("review".equals(bankSmsFilter)) {
            return "نیازمند بررسی";
        }
        if ("duplicate".equals(bankSmsFilter)) {
            return "تکراری";
        }
        return "همه";
    }

    private Map<String, ConversationSummary> buildConversationSummaries(HistoryStore.Entry[] entries) {
        Map<String, ConversationSummary> conversations = new LinkedHashMap<>();
        for (HistoryStore.Entry entry : entries) {
            if (isSystemSmsEntry(entry)) {
                continue;
            }
            String bank = entryBank(entry);
            String sender = entrySender(entry);
            String key = conversationKey(sender);
            ConversationSummary summary = conversations.get(key);
            if (summary == null) {
                summary = new ConversationSummary(key, bank, sender, entry);
                conversations.put(key, summary);
            }
            summary.total++;
            if (isConfirmedPaymentEntry(entry)) {
                summary.approved++;
            } else if (entry.rejected && !isSoftReviewEntry(entry)) {
                summary.rejected++;
            }
        }
        return conversations;
    }

    private void addConversationRow(LinearLayout parent, final ConversationSummary summary) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), dp(12), dp(10), dp(12));
        styleGradientRounded(row, cardColor, inputColor, strokeColor, dp(24));
        LinearLayout.LayoutParams rowLp = matchWrap();
        rowLp.setMargins(0, dp(6), 0, dp(6));
        parent.addView(row, rowLp);
        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                selectedConversationKey = summary.key;
                renderBankSmsBubbles();
                scrollView.smoothScrollTo(0, 0);
            }
        });

        TextView avatar = new TextView(this);
        avatar.setText(summary.bank.length() > 0 ? summary.bank.substring(0, 1) : "ب");
        avatar.setTextSize(21);
        avatar.setTypeface(Typeface.DEFAULT_BOLD);
        avatar.setTextColor(Color.parseColor("#07111F"));
        avatar.setGravity(Gravity.CENTER);
        styleCircle(avatar, avatarColor(summary.key));
        LinearLayout.LayoutParams avatarLp = new LinearLayout.LayoutParams(dp(56), dp(56));
        avatarLp.setMargins(dp(8), 0, dp(10), 0);
        row.addView(avatar, avatarLp);

        LinearLayout textBox = new LinearLayout(this);
        textBox.setOrientation(LinearLayout.VERTICAL);
        row.addView(textBox, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView sender = new TextView(this);
        sender.setText(summary.sender);
        sender.setTextSize(17);
        sender.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        sender.setTextColor(textColor);
        textBox.addView(sender, matchWrap());

        TextView bank = new TextView(this);
        bank.setText(summary.bank + " • " + summary.total + " پیام");
        bank.setTextSize(12);
        bank.setTextColor(mutedColor);
        textBox.addView(bank, matchWrap());

        TextView preview = new TextView(this);
        preview.setText(shortPreview(summary.latest.title + " — " + transactionPreview(summary.latest)));
        preview.setTextSize(12);
        preview.setTextColor(mutedColor);
        preview.setSingleLine(true);
        textBox.addView(preview, matchWrap());

        TextView time = new TextView(this);
        time.setText(shortTime(summary.latest.time) + "\n" + summaryStatus(summary));
        time.setTextSize(12);
        time.setGravity(Gravity.CENTER);
        time.setTextColor(summary.rejected > 0 ? goldColor : (summary.approved > 0 ? greenColor : mutedColor));
        row.addView(time, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));
    }

    private void renderConversationThread(final ConversationSummary summary, HistoryStore.Entry[] entries) {
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setPadding(0, 0, 0, dp(8));
        bankSmsListView.addView(head, matchWrap());

        Button back = new Button(this);
        back.setText("⬅️ لیست");
        styleButton(back, false);
        head.addView(back, new LinearLayout.LayoutParams(dp(96), LinearLayout.LayoutParams.WRAP_CONTENT));
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                selectedConversationKey = "";
                renderBankSmsBubbles();
            }
        });

        TextView title = new TextView(this);
        title.setText(summary.sender + "\n" + summary.bank);
        title.setTextSize(16);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(textColor);
        title.setGravity(Gravity.CENTER);
        head.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        for (HistoryStore.Entry entry : entries) {
            if (isSystemSmsEntry(entry)) {
                continue;
            }
            String bank = entryBank(entry);
            String sender = entrySender(entry);
            String key = conversationKey(sender);
            if (summary.key.equals(key)) {
                addSmsBubble(bankSmsListView, entry);
            }
        }
    }

    private void addSmsBubble(LinearLayout parent, HistoryStore.Entry entry) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(isConfirmedPaymentEntry(entry) ? Gravity.RIGHT : Gravity.LEFT);
        row.setPadding(0, dp(5), 0, dp(5));
        parent.addView(row, matchWrap());

        LinearLayout stack = new LinearLayout(this);
        stack.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                (int) (getResources().getDisplayMetrics().widthPixels * 0.82f),
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        row.addView(stack, lp);

        TextView bubble = new TextView(this);
        bubble.setText(formatSmsBubble(entry));
        bubble.setTextSize(13);
        bubble.setTextColor(textColor);
        bubble.setTextIsSelectable(true);
        bubble.setLineSpacing(dp(2), 1.08f);
        bubble.setPadding(dp(15), dp(13), dp(15), dp(13));
        boolean softReview = isSoftReviewEntry(entry);
        boolean confirmed = isConfirmedPaymentEntry(entry);
        int fill = confirmed ? approvedColor : (entry.rejected && !softReview ? rejectedColor : neutralColor);
        int border = confirmed ? greenColor : (entry.rejected && !softReview ? Color.parseColor("#EF4444") : goldColor);
        styleGradientRounded(bubble, fill, inputColor, border, dp(22));
        stack.addView(bubble, matchWrap());

        if (isManualApproveCandidate(entry)) {
            Button approveButton = new Button(this);
            approveButton.setText("✅ تایید دستی و افزودن به درآمد");
            styleButton(approveButton, true);
            LinearLayout.LayoutParams buttonLp = matchWrap();
            buttonLp.setMargins(0, dp(6), 0, 0);
            stack.addView(approveButton, buttonLp);
            approveButton.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    approveEntryManually(entry);
                }
            });
        }
    }

    private String formatSmsBubble(HistoryStore.Entry entry) {
        String bank = entryBank(entry);
        String sender = entrySender(entry);
        String status = smsStatusText(entry);
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }

        StringBuilder out = new StringBuilder();
        out.append(status).append("\n");
        out.append(sender).append("  •  ").append(bank).append("\n");
        out.append("━━━━━━━━━━━━").append("\n");
        if (!rawSms.isEmpty()) {
            out.append(formatRawSms(rawSms)).append("\n");
        } else {
            out.append(compactSmsDetail(entry.detail)).append("\n");
        }
        out.append("━━━━━━━━━━━━").append("\n");

        String amount = extractDetailLine(entry.detail, "💵 معادل تقریبی:");
        String rawAmount = extractDetailLine(entry.detail, "💰 مبلغ SMS:");
        String reference = extractDetailLine(entry.detail, "🔖 پیگیری:");
        String response = robotResponseFromDetail(entry.detail);
        if (!amount.isEmpty()) {
            out.append("💰 مبلغ ربات: ").append(amount).append("\n");
        } else if (!rawAmount.isEmpty()) {
            out.append("💰 مبلغ SMS: ").append(rawAmount).append("\n");
        }
        if (!reference.isEmpty() && !"-".equals(reference)) {
            out.append("🔖 پیگیری: ").append(reference).append("\n");
        }
        if (!response.isEmpty()) {
            out.append("🤖 نتیجه ربات: ").append(response).append("\n");
        }
        out.append("🕒 ").append(entry.time);
        return out.toString();
    }

    private boolean isManualApproveCandidate(HistoryStore.Entry entry) {
        if (entry == null || isConfirmedPaymentEntry(entry) || isDuplicateApprovedEntry(entry)) {
            return false;
        }
        if (extractEntryTomanAmount(entry) <= 0) {
            return false;
        }
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }
        if (!rawSms.isEmpty() && !PaymentSmsParser.isIncomingPayment(rawSms)) {
            return false;
        }
        String text = ((entry.title == null ? "" : entry.title) + "\n" + (entry.detail == null ? "" : entry.detail)).toLowerCase(Locale.US);
        if (text.contains("sms_reused") || text.contains("قبلاً برای پرداخت دیگری استفاده")) {
            return false;
        }
        return entry.rejected
                || text.contains("no_pending_match")
                || text.contains("pending پیدا نشد")
                || text.contains("پرداخت در انتظار پیدا نشد")
                || text.contains("ambiguous")
                || text.contains("چند پرداخت")
                || text.contains("ارسال/ثبت شده")
                || text.contains("به ربات ارسال شد");
    }

    private void approveEntryManually(HistoryStore.Entry entry) {
        String eventId = extractDetailLine(entry.detail, "🔐 شناسه داخلی:");
        if (eventId.isEmpty()) {
            eventId = entryFingerprint(entry);
        }
        if (eventId.isEmpty()) {
            Toast.makeText(this, "شناسه این پیامک پیدا نشد", Toast.LENGTH_LONG).show();
            return;
        }
        boolean ok = HistoryStore.markBankSmsManuallyApproved(this, eventId);
        if (ok) {
            IncomeStore.record(
                    this,
                    eventId,
                    extractEntryTomanAmount(entry),
                    "تایید دستی داخل اپ",
                    entryBank(entry),
                    entrySender(entry),
                    extractDetailLine(entry.detail, "🔖 پیگیری:"),
                    System.currentTimeMillis()
            );
            refreshHistory();
            Toast.makeText(this, "پیامک دستی تایید شد و به درآمد اضافه شد", Toast.LENGTH_LONG).show();
        } else {
            Toast.makeText(this, "این پیامک برای تایید دستی پیدا نشد", Toast.LENGTH_LONG).show();
        }
    }

    private String summaryStatus(ConversationSummary summary) {
        if (summary.rejected > 0) {
            return summary.rejected + " نیازمند بررسی";
        }
        if (summary.approved > 0) {
            return summary.approved + " تاییدشده";
        }
        return "در انتظار";
    }

    private String shortPreview(String text) {
        if (text == null) {
            return "";
        }
        String oneLine = text.replace("\n", " ").trim();
        return oneLine.length() > 72 ? oneLine.substring(0, 72) + "..." : oneLine;
    }

    private String transactionPreview(HistoryStore.Entry entry) {
        if (entry == null) {
            return "";
        }
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }
        String amount = extractDetailLine(entry.detail, "💵 معادل تقریبی:");
        if (amount.isEmpty()) {
            amount = extractDetailLine(entry.detail, "💰 مبلغ SMS:");
        }
        String response = robotResponseFromDetail(entry.detail);
        String reference = extractDetailLine(entry.detail, "🔖 پیگیری:");
        StringBuilder out = new StringBuilder();
        if (!amount.isEmpty()) {
            out.append(amount);
        }
        if (!response.isEmpty()) {
            if (out.length() > 0) {
                out.append(" • ");
            }
            out.append(response);
        }
        if (!reference.isEmpty() && !"-".equals(reference)) {
            if (out.length() > 0) {
                out.append(" • ");
            }
            out.append("پیگیری ").append(reference);
        }
        if (out.length() == 0 && !rawSms.isEmpty()) {
            out.append(shortPreview(rawSms));
        }
        return out.length() > 0 ? out.toString() : compactSmsDetail(entry.detail);
    }

    private String shortTime(String time) {
        if (time == null) {
            return "";
        }
        String value = time.trim();
        if (value.length() >= 16) {
            return value.substring(11, 16);
        }
        return value;
    }

    private int avatarColor(String key) {
        int[] colors = {
                Color.parseColor("#0EA5E9"),
                Color.parseColor("#EC4899"),
                Color.parseColor("#22C55E"),
                Color.parseColor("#F59E0B"),
                Color.parseColor("#8B5CF6")
        };
        return colors[(key.hashCode() & 0x7fffffff) % colors.length];
    }

    private static final class ConversationSummary {
        final String key;
        final String bank;
        final String sender;
        final HistoryStore.Entry latest;
        int total;
        int approved;
        int rejected;

        ConversationSummary(String key, String bank, String sender, HistoryStore.Entry latest) {
            this.key = key;
            this.bank = bank;
            this.sender = sender;
            this.latest = latest;
        }
    }

    private String compactSmsDetail(String detail) {
        if (detail == null || detail.trim().isEmpty()) {
            return "جزئیات ثبت نشده است.";
        }
        String text = detail.trim();
        int rawIndex = text.indexOf("📄 متن SMS:");
        if (rawIndex < 0) {
            rawIndex = text.indexOf("متن SMS:");
        }
        if (rawIndex >= 0) {
            text = text.substring(0, rawIndex).trim();
        }
        text = text.replace("\n🌐 کد HTTP:", "\nکد HTTP:");
        text = text.replace("\n🧾 پاسخ ربات:", "\nپاسخ ربات:");
        text = text.replaceAll("(?m)^🔐 شناسه داخلی:.*\\n?", "");
        if (text.length() > 520) {
            return text.substring(0, 520) + "...";
        }
        return text;
    }

    private boolean shouldDisplayBankEntry(HistoryStore.Entry entry, SettingsStore settings) {
        if (entry == null || settings == null) {
            return false;
        }
        String sender = entrySender(entry);
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }
        if (!rawSms.isEmpty() && !PaymentSmsParser.isIncomingPayment(rawSms)) {
            return false;
        }
        String configured = settings.getMatchedConfiguredBankName(sender, rawSms);
        if (!configured.isEmpty()) {
            return true;
        }
        if (!rawSms.isEmpty()) {
            return false;
        }
        String bank = entryBankRaw(entry);
        return settings.isActiveBankName(bank);
    }

    private String entryFingerprint(HistoryStore.Entry entry) {
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }
        String sender = conversationKey(entrySender(entry));
        if (!rawSms.isEmpty()) {
            return "sms:" + sender + ":" + rawSms.hashCode();
        }
        String internalId = extractDetailLine(entry.detail, "🔐 شناسه داخلی:");
        if (!internalId.isEmpty()) {
            return "id:" + internalId;
        }
        String amount = extractDetailLine(entry.detail, "💰 مبلغ SMS:");
        String reference = extractDetailLine(entry.detail, "🔖 پیگیری:");
        return "meta:" + sender + ":" + amount + ":" + reference + ":" + (entry.title == null ? "" : entry.title);
    }

    private boolean isSystemSmsEntry(HistoryStore.Entry entry) {
        if (entry == null) {
            return true;
        }
        String text = (entry.title == null ? "" : entry.title) + "\n" + (entry.detail == null ? "" : entry.detail);
        return text.contains("بررسی پیامک‌های قبلی")
                || text.contains("INBOX_SCAN")
                || text.contains("تعداد پیامک‌های بررسی‌شده")
                || text.contains("Scanning last");
    }

    private String entryBank(HistoryStore.Entry entry) {
        String sender = entrySender(entry);
        String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
        if (rawSms.isEmpty()) {
            rawSms = extractDetailBlock(entry.detail, "متن SMS:");
        }
        String configured = new SettingsStore(this).getMatchedConfiguredBankName(sender, rawSms);
        if (!configured.isEmpty()) {
            return configured;
        }
        String rawBank = entryBankRaw(entry);
        return rawBank.isEmpty() || "تنظیمات عمومی".equals(rawBank) ? "پیامک بانکی" : rawBank;
    }

    private String entryBankRaw(HistoryStore.Entry entry) {
        String bank = extractDetailLine(entry.detail, "🏦 بانک:");
        if (bank.isEmpty()) {
            bank = extractDetailLine(entry.detail, "بانک:");
        }
        if (bank.isEmpty()) {
            String rawSms = extractDetailBlock(entry.detail, "📄 متن SMS:");
            if (rawSms.isEmpty()) {
                rawSms = extractDetailBlock(entry.detail, "متن SMS:");
            }
            String firstLine = rawSms.split("\\n", 2)[0].trim();
            if (!firstLine.isEmpty()) {
                bank = firstLine;
            }
        }
        return bank.isEmpty() ? "پیامک بانکی" : bank;
    }

    private String entrySender(HistoryStore.Entry entry) {
        String sender = extractDetailLine(entry.detail, "👤 سرشماره:");
        if (sender.isEmpty()) {
            sender = extractDetailLine(entry.detail, "فرستنده:");
        }
        if (sender.isEmpty()) {
            sender = extractDetailLine(entry.detail, "Sender=");
        }
        return sender.isEmpty() ? "بدون سرشماره" : sender;
    }

    private String conversationKey(String sender) {
        String value = sender == null ? "" : sender.trim().toLowerCase(Locale.US);
        String digits = PaymentSmsParser.normalizeDigits(value).replaceAll("[^0-9]", "");
        if (digits.startsWith("0098") && digits.length() > 6) {
            digits = "0" + digits.substring(4);
        } else if (digits.startsWith("98") && digits.length() > 10) {
            digits = "0" + digits.substring(2);
        }
        return digits.length() >= 4 ? "sender:" + digits : "sender:" + value;
    }

    private String humanRobotResponse(String response) {
        if (response == null || response.trim().isEmpty() || "-".equals(response.trim())) {
            return "پاسخی ثبت نشده";
        }
        String text = response.trim();
        String lower = text.toLowerCase(Locale.US);
        String compact = WebhookClient.compactResponse(text);
        if (compact.contains("\"status\":\"sms_reused\"") || text.contains("قبلاً برای پرداخت دیگری استفاده")) {
            return "این SMS قبلاً برای پرداخت دیگری استفاده شده است";
        }
        if (compact.contains("\"status\":\"approved\"")
                || compact.contains("\"matched\":true")
                || (compact.contains("\"matched_payment_id\":") && !compact.contains("\"matched_payment_id\":0"))
                || text.contains("تایید شد")) {
            return "پرداخت تایید شد";
        }
        if (compact.contains("\"status\":\"no_pending_match\"") || lower.contains("no_pending_match") || text.contains("pending پیدا نشد") || text.contains("پرداخت pending پیدا نشد")) {
            return "پرداخت در انتظار با این مبلغ پیدا نشد";
        }
        if (lower.contains("ambiguous") || text.contains("چند پرداخت")) {
            return "چند پرداخت مشابه پیدا شد؛ نیازمند بررسی ادمین";
        }
        if (lower.contains("\"retry\":true")) {
            return "فعلاً تایید نشد؛ بعداً دوباره قابل بررسی است";
        }
        if (text.length() > 90) {
            return "پاسخ ربات دریافت شد، اما تایید قطعی نبود";
        }
        return text;
    }

    private String robotResponseFromDetail(String detail) {
        String all = detail == null ? "" : detail;
        String lower = all.toLowerCase(Locale.US);
        boolean hasRobotStatus = all.contains("🧾 پاسخ ربات:")
                || all.contains("📨 نتیجه:")
                || lower.contains("\"status\"")
                || lower.contains("no_pending_match")
                || lower.contains("ambiguous")
                || lower.contains("\"matched\"")
                || lower.contains("matched_payment_id")
                || all.contains("pending پیدا نشد")
                || all.contains("تایید شد");
        if (!hasRobotStatus) {
            return "";
        }
        String line = extractDetailLine(detail, "🧾 پاسخ ربات:");
        String combined = line + "\n" + all;
        return humanRobotResponse(combined);
    }

    private boolean isSoftReviewEntry(HistoryStore.Entry entry) {
        String text = ((entry.title == null ? "" : entry.title) + "\n" + (entry.detail == null ? "" : entry.detail)).toLowerCase(Locale.US);
        return text.contains("no_pending_match")
                || text.contains("پرداخت در انتظار پیدا نشد")
                || text.contains("pending پیدا نشد")
                || text.contains("sms_reused")
                || text.contains("قبلاً برای پرداخت دیگری استفاده")
                || isDuplicateApprovedEntry(entry);
    }

    private boolean isDuplicateApprovedEntry(HistoryStore.Entry entry) {
        String text = ((entry == null || entry.title == null ? "" : entry.title) + "\n" + (entry == null || entry.detail == null ? "" : entry.detail)).toLowerCase(Locale.US);
        return text.contains("sms_reused")
                || text.contains("قبلاً برای پرداخت دیگری استفاده");
    }

    private boolean isConfirmedPaymentEntry(HistoryStore.Entry entry) {
        return entry != null && (entry.approved || isManualApprovedEntry(entry) || hasApprovedRobotResult(entry)) && !isDuplicateApprovedEntry(entry);
    }

    private boolean isManualApprovedEntry(HistoryStore.Entry entry) {
        String eventId = extractDetailLine(entry == null ? "" : entry.detail, "🔐 شناسه داخلی:");
        if (!eventId.isEmpty() && HistoryStore.isManuallyApproved(this, eventId)) {
            return true;
        }
        if ((eventId == null || eventId.isEmpty()) && entry != null) {
            String fingerprint = entryFingerprint(entry);
            if (!fingerprint.isEmpty() && HistoryStore.isManuallyApproved(this, fingerprint)) {
                return true;
            }
        }
        String text = (entry == null || entry.title == null ? "" : entry.title)
                + "\n" + (entry == null || entry.detail == null ? "" : entry.detail);
        return text.contains("تایید دستی داخل اپ");
    }

    private boolean hasApprovedRobotResult(HistoryStore.Entry entry) {
        if (entry == null) {
            return false;
        }
        String text = ((entry.title == null ? "" : entry.title) + "\n" + (entry.detail == null ? "" : entry.detail));
        String lower = text.toLowerCase(Locale.US);
        String compact = WebhookClient.compactResponse(text);
        if (compact.contains("\"status\":\"sms_reused\"")
                || compact.contains("\"status\":\"no_pending_match\"")
                || lower.contains("no_pending_match")
                || lower.contains("ambiguous")
                || text.contains("پرداخت در انتظار پیدا نشد")
                || text.contains("pending پیدا نشد")
                || text.contains("قبلاً برای پرداخت دیگری استفاده")
                || text.contains("چند پرداخت")) {
            return false;
        }
        return compact.contains("\"status\":\"approved\"")
                || compact.contains("\"status\":\"approved_duplicate\"")
                || compact.contains("\"matched\":true")
                || (compact.contains("\"matched_payment_id\":") && !compact.contains("\"matched_payment_id\":0"))
                || text.contains("پاسخ ربات: تایید شد")
                || text.contains("نتیجه ربات: تایید شد")
                || text.contains("قبلاً تایید شده بود");
    }

    private String smsStatusText(HistoryStore.Entry entry) {
        String text = ((entry.title == null ? "" : entry.title) + "\n" + (entry.detail == null ? "" : entry.detail)).toLowerCase(Locale.US);
        if (isDuplicateApprovedEntry(entry)) {
            return "🟡 SMS تکراری؛ تایید جدید نیست";
        }
        if (isManualApprovedEntry(entry)) {
            return "✅ تایید دستی توسط شما";
        }
        if (isConfirmedPaymentEntry(entry)) {
            return "✅ تایید شده توسط اپ";
        }
        if (text.contains("no_pending_match") || text.contains("پرداخت در انتظار پیدا نشد") || text.contains("pending پیدا نشد")) {
            return "🟡 پرداخت در انتظار پیدا نشد";
        }
        if (text.contains("sms_reused") || text.contains("قبلاً برای پرداخت دیگری استفاده")) {
            return "🟡 SMS تکراری؛ بررسی ادمین";
        }
        if (text.contains("ambiguous") || text.contains("چند پرداخت")) {
            return "⚠️ چند پرداخت مشابه؛ بررسی ادمین";
        }
        if (entry.rejected) {
            return "⚠️ تایید نشد؛ بررسی لازم است";
        }
        return "📨 ارسال/ثبت شده";
    }

    private String extractDetailBlock(String detail, String marker) {
        if (detail == null || marker == null || marker.trim().isEmpty()) {
            return "";
        }
        int index = detail.indexOf(marker);
        if (index < 0) {
            return "";
        }
        return detail.substring(index + marker.length()).trim();
    }

    private String formatRawSms(String rawSms) {
        String value = rawSms == null ? "" : rawSms.trim();
        value = value.replace("\r", "\n").replaceAll("\\n{3,}", "\n\n");
        return value.length() > 900 ? value.substring(0, 900) + "..." : value;
    }

    private String extractDetailLine(String detail, String prefix) {
        if (detail == null || detail.trim().isEmpty()) {
            return "";
        }
        String[] lines = detail.split("\\n");
        for (String line : lines) {
            String text = line == null ? "" : line.trim();
            if (text.startsWith(prefix)) {
                return text.substring(prefix.length()).trim();
            }
        }
        return "";
    }

    private String makeLogReadable(String history) {
        return history
                .replace("\n---\n", "\n\n━━━━━━━━━━━━━━━━\n\n")
                .replace("NO_PENDING_MATCH", "پرداخت پیدا نشد")
                .replace("APPROVED_DUPLICATE", "قبلاً تایید شده")
                .replace("APPROVED", "تایید شده");
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
        styleGradientRounded(spinner, inputColor, cardColor, strokeColor, dp(14));
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
        input.setPadding(dp(12), dp(8), dp(12), dp(8));
        input.setSelectAllOnFocus(true);
        styleGradientRounded(input, inputColor, cardColor, strokeColor, dp(15));
        input.setInputType(secret
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        root.addView(input, matchWrap());
        return input;
    }

    private LinearLayout addCard(LinearLayout root) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(13), dp(12), dp(13), dp(13));
        styleGradientRounded(card, cardColor, glassEndColor, strokeColor, dp(22));
        LinearLayout.LayoutParams lp = matchWrap();
        lp.setMargins(0, dp(8), 0, dp(8));
        root.addView(card, lp);
        return card;
    }

    private LinearLayout createTopMenuPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(14), dp(12), dp(14), dp(14));
        styleGradientRounded(panel, glassStartColor, glassEndColor, goldColor, dp(24));
        return panel;
    }

    private void addPageHeader(LinearLayout root, String titleText, String hintText) {
        LinearLayout header = addDashboardCard(root);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(row, matchWrap());

        Button back = new Button(this);
        back.setText("⬅️");
        styleButton(back, false);
        row.addView(back, new LinearLayout.LayoutParams(dp(48), dp(42)));
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showMainScreen();
            }
        });

        TextView title = new TextView(this);
        title.setText(titleText);
        title.setTextSize(20);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(textColor);
        title.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        row.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView hint = new TextView(this);
        hint.setText(hintText);
        hint.setTextSize(12);
        hint.setTextColor(mutedColor);
        hint.setGravity(Gravity.RIGHT);
        hint.setLineSpacing(0, 1.15f);
        hint.setPadding(0, dp(9), 0, 0);
        header.addView(hint, matchWrap());
    }

    private void showTopMenu(View anchor) {
        if (topMenuPopup != null && topMenuPopup.isShowing()) {
            topMenuPopup.dismiss();
            return;
        }
        int screenWidth = getResources().getDisplayMetrics().widthPixels;
        int width = Math.min(screenWidth - dp(28), dp(360));
        topMenuPopup = new PopupWindow(
                topMenuPanel,
                width,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                true
        );
        topMenuPopup.setOutsideTouchable(true);
        topMenuPopup.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            topMenuPopup.setElevation(dp(12));
        }
        topMenuPopup.showAsDropDown(anchor, 0, dp(8));
    }

    private void dismissTopMenu() {
        if (topMenuPopup != null && topMenuPopup.isShowing()) {
            topMenuPopup.dismiss();
        }
    }

    private LinearLayout addDashboardCard(LinearLayout root) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(13), dp(14), dp(14));
        styleGradientRounded(card, glassStartColor, glassEndColor, strokeColor, dp(26));
        LinearLayout.LayoutParams lp = matchWrap();
        lp.setMargins(0, dp(8), 0, dp(10));
        root.addView(card, lp);
        return card;
    }

    private TextView addMetricCard(LinearLayout row, String icon, String label, int fill, int border, View.OnClickListener listener) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setGravity(Gravity.CENTER);
        card.setPadding(dp(8), dp(10), dp(8), dp(10));
        styleGradientRounded(card, fill, inputColor, border, dp(20));
        LinearLayout.LayoutParams lp = weightedButtonLp();
        lp.setMargins(dp(4), dp(3), dp(4), dp(3));
        row.addView(card, lp);
        if (listener != null) {
            card.setOnClickListener(listener);
            card.setClickable(true);
        }

        TextView iconView = new TextView(this);
        iconView.setText(icon);
        iconView.setTextSize(18);
        iconView.setGravity(Gravity.CENTER);
        card.addView(iconView, matchWrap());

        TextView valueView = new TextView(this);
        valueView.setText("0");
        valueView.setTextSize(24);
        valueView.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        valueView.setTextColor(textColor);
        valueView.setGravity(Gravity.CENTER);
        card.addView(valueView, matchWrap());

        TextView labelView = new TextView(this);
        labelView.setText(label);
        labelView.setTextSize(11);
        labelView.setTextColor(mutedColor);
        labelView.setGravity(Gravity.CENTER);
        card.addView(labelView, matchWrap());
        return valueView;
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
            row.addView(button, weightedButtonLp());
        }
    }

    private void addSectionTitle(LinearLayout root, String text) {
        TextView section = new TextView(this);
        section.setText(text);
        section.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        section.setTextColor(textColor);
        section.setTextSize(16);
        section.setPadding(0, dp(3), 0, dp(9));
        root.addView(section, matchWrap());
    }

    private void styleButton(Button button, boolean primary) {
        button.setAllCaps(false);
        button.setTextSize(12);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        button.setTextColor(primary ? Color.parseColor("#07111F") : textColor);
        button.setMinHeight(dp(42));
        button.setMinWidth(0);
        button.setPadding(dp(9), dp(5), dp(9), dp(5));
        if (primary) {
            styleGradientRounded(button, goldColor, greenColor, goldColor, dp(18));
        } else {
            styleGradientRounded(button, inputColor, cardColor, strokeColor, dp(18));
        }
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

    private void styleGradientRounded(View view, int start, int end, int stroke, int radius) {
        GradientDrawable bg = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{start, end}
        );
        bg.setStroke(dp(1), stroke);
        bg.setCornerRadius(radius);
        view.setBackground(bg);
    }

    private void stylePageBackground(View view) {
        GradientDrawable bg = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{pageTopColor, bgColor, pageBottomColor}
        );
        view.setBackground(bg);
    }

    private void styleCircle(View view, int fill) {
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(fill);
        view.setBackground(bg);
    }

    private void loadPalette() {
        SettingsStore settings = new SettingsStore(this);
        boolean dark = isDarkMode(settings.getThemeMode());
        bgColor = Color.parseColor(dark ? "#07111F" : "#F3F7F2");
        pageTopColor = Color.parseColor(dark ? "#0B1628" : "#FFFFFF");
        pageBottomColor = Color.parseColor(dark ? "#020617" : "#E9F5EC");
        cardColor = Color.parseColor(dark ? "#101B2D" : "#FFFFFF");
        inputColor = Color.parseColor(dark ? "#162338" : "#F8FAF7");
        textColor = Color.parseColor(dark ? "#F8FAFC" : "#102017");
        mutedColor = Color.parseColor(dark ? "#A7B0C2" : "#607066");
        strokeColor = Color.parseColor(dark ? "#2A3B55" : "#D2DDCE");
        primaryColor = Color.parseColor("#22C55E");
        goldColor = Color.parseColor(dark ? "#F5C542" : "#D69B00");
        greenColor = Color.parseColor(dark ? "#35E07B" : "#16A34A");
        glassStartColor = Color.parseColor(dark ? "#14233A" : "#FFFFFF");
        glassEndColor = Color.parseColor(dark ? "#0A1220" : "#ECF7EF");
        softGoldColor = Color.parseColor(dark ? "#3B2F12" : "#FFF4C7");
        softGreenColor = Color.parseColor(dark ? "#0D3328" : "#DCFCE7");
        approvedColor = Color.parseColor(dark ? "#0F3D2E" : "#DCFCE7");
        rejectedColor = Color.parseColor(dark ? "#4A151A" : "#FEE2E2");
        neutralColor = Color.parseColor(dark ? "#3B2F12" : "#FEF3C7");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(bgColor);
            getWindow().setNavigationBarColor(bgColor);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            int flags = getWindow().getDecorView().getSystemUiVisibility();
            if (dark) {
                flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    flags &= ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                }
            } else {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                }
            }
            getWindow().getDecorView().setSystemUiVisibility(flags);
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

    private LinearLayout.LayoutParams weightedButtonLp() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        lp.setMargins(dp(3), 0, dp(3), 0);
        return lp;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
