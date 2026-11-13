package com.mumin.aitrader;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public final class MainActivity extends Activity {
    private static final String REPORT_URL =
            "https://raw.githubusercontent.com/IskanderKhemiri1234/Mumin-AI-Trader/mumin-paper-ledger/latest-report.html";

    private WebView webView;
    private TextView statusView;
    private boolean showingOfflinePage;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(7, 23, 19));
        getWindow().setNavigationBarColor(Color.rgb(7, 23, 19));
        buildScreen();
        loadLatestReport(false);
    }

    private void buildScreen() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(7, 23, 19));

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(16), dp(9), dp(12), dp(9));
        toolbar.setBackgroundColor(Color.rgb(9, 31, 24));

        TextView brand = new TextView(this);
        brand.setText("MUMIN  ·  AI TRADER");
        brand.setTextColor(Color.rgb(229, 201, 130));
        brand.setTextSize(14);
        brand.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        toolbar.addView(brand, new LinearLayout.LayoutParams(0, dp(42), 1));

        Button refresh = new Button(this);
        refresh.setText("تحديث  ↻");
        refresh.setTextColor(Color.rgb(245, 243, 233));
        refresh.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(27, 59, 43)));
        refresh.setOnClickListener(v -> loadLatestReport(true));
        toolbar.addView(refresh, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, dp(42)));

        root.addView(toolbar);

        statusView = new TextView(this);
        statusView.setText("جارٍ الاتصال بأحدث تقرير Paper…");
        statusView.setTextColor(Color.rgb(158, 181, 168));
        statusView.setTextSize(11);
        statusView.setPadding(dp(16), dp(7), dp(16), dp(7));
        root.addView(statusView);

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(7, 23, 19));
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(false);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportMultipleWindows(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            settings.setSafeBrowsingEnabled(true);
        }

        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String host = request.getUrl().getHost();
                return host == null || !host.equals("raw.githubusercontent.com");
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                if (!showingOfflinePage) {
                    statusView.setText("جارٍ جلب أحدث نسخة من فرع بيانات Paper…");
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!showingOfflinePage) {
                    statusView.setText("لوحة Paper · للقراءة فقط");
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request.isForMainFrame() && !showingOfflinePage) {
                    showOfflinePage();
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                if (!showingOfflinePage) {
                    showOfflinePage();
                }
            }
        });

        root.addView(webView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
    }

    private void loadLatestReport(boolean forceRefresh) {
        showingOfflinePage = false;
        statusView.setText("جارٍ جلب أحدث تقرير Paper…");
        if (forceRefresh) {
            webView.clearCache(false);
            Toast.makeText(this, "جارٍ تحديث بيانات Paper", Toast.LENGTH_SHORT).show();
        }
        webView.loadUrl(REPORT_URL);
    }

    private void showOfflinePage() {
        showingOfflinePage = true;
        statusView.setText("تعذر الاتصال · البيانات غير محدثة");
        webView.loadUrl("file:///android_asset/offline.html");
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
