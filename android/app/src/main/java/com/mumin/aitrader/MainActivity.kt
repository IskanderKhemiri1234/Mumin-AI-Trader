package com.mumin.aitrader

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import android.app.Activity

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var progress: ProgressBar

    companion object {
        private const val REPORT_URL =
            "https://raw.githubusercontent.com/IskanderKhemiri1234/Mumin-AI-Trader/mumin-paper-ledger/latest-report.html"
        private const val EMERALD = 0xFF071C17.toInt()
        private const val GOLD = 0xFFD8B66A.toInt()
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.statusBarColor = EMERALD
        window.navigationBarColor = EMERALD

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(EMERALD)
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(14), dp(20), dp(12))
            setBackgroundColor(EMERALD)
        }
        header.addView(TextView(this).apply {
            text = "MUMIN AI TRADER"
            setTextColor(GOLD)
            textSize = 18f
            letterSpacing = 0.12f
            setTypeface(null, android.graphics.Typeface.BOLD)
        })
        header.addView(TextView(this).apply {
            text = "PAPER PORTFOLIO  •  70 USDT"
            setTextColor(Color.rgb(190, 205, 197))
            textSize = 11f
            letterSpacing = 0.08f
            setPadding(0, dp(4), 0, 0)
        })
        root.addView(header)

        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true
            indeterminateTintList = android.content.res.ColorStateList.valueOf(GOLD)
            visibility = View.GONE
        }
        root.addView(progress, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(3)
        ))

        swipeRefresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors(GOLD)
            setProgressBackgroundColorSchemeColor(EMERALD)
        }
        webView = WebView(this).apply {
            setBackgroundColor(EMERALD)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            settings.loadsImagesAutomatically = true
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.setSupportMultipleWindows(false)
            settings.safeBrowsingEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    return request.url.scheme != "https" ||
                        request.url.host != "raw.githubusercontent.com"
                }

                override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                    progress.visibility = View.VISIBLE
                }

                override fun onPageFinished(view: WebView, url: String) {
                    progress.visibility = View.GONE
                    swipeRefresh.isRefreshing = false
                }

                override fun onReceivedError(
                    view: WebView,
                    request: WebResourceRequest,
                    error: WebResourceError
                ) {
                    if (request.isForMainFrame) {
                        progress.visibility = View.GONE
                        swipeRefresh.isRefreshing = false
                        view.loadDataWithBaseURL(
                            null,
                            """<html><meta name="viewport" content="width=device-width, initial-scale=1">
                            <body style="margin:0;background:#071C17;color:#F4F0E6;font-family:sans-serif;
                            display:grid;place-items:center;min-height:90vh;text-align:center;padding:24px">
                            <div><h2 style="color:#D8B66A">Mumin AI Trader</h2>
                            <p>تعذر جلب آخر تقرير. تحقق من اتصال الإنترنت ثم اسحب الشاشة للأسفل لإعادة المحاولة.</p>
                            <p style="color:#9BAFA6;font-size:12px">The Paper engine is separate from this viewer.</p>
                            </div></body></html>""",
                            "text/html",
                            "UTF-8",
                            null
                        )
                    }
                }
            }
            webChromeClient = WebChromeClient()
        }
        swipeRefresh.addView(webView, SwipeRefreshLayout.LayoutParams(
            SwipeRefreshLayout.LayoutParams.MATCH_PARENT,
            SwipeRefreshLayout.LayoutParams.MATCH_PARENT
        ))
        swipeRefresh.setOnRefreshListener { loadLatestReport() }
        root.addView(swipeRefresh, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f
        ))

        setContentView(root)
        if (savedInstanceState == null) {
            loadLatestReport()
        } else {
            webView.restoreState(savedInstanceState)
            if (webView.url.isNullOrBlank()) loadLatestReport()
        }
    }

    private fun loadLatestReport() {
        progress.visibility = View.VISIBLE
        webView.loadUrl(REPORT_URL, mapOf(
            "Cache-Control" to "no-cache",
            "Pragma" to "no-cache"
        ))
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    override fun onSaveInstanceState(outState: Bundle) {
        webView.saveState(outState)
        super.onSaveInstanceState(outState)
    }

    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.destroy()
        }
        super.onDestroy()
    }
}
