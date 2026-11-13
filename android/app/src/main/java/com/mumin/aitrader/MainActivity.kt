package com.mumin.aitrader

import android.annotation.SuppressLint
import android.app.Activity
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout

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
            text = "ROYAL EDITION  •  PAPER PORTFOLIO"
            setTextColor(Color.rgb(190, 205, 197))
            textSize = 11f
            letterSpacing = 0.08f
            setPadding(0, dp(4), 0, 0)
        })
        root.addView(header)

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
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                settings.safeBrowsingEnabled = true
            }
            webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(
                    view: WebView,
                    request: WebResourceRequest
                ): WebResourceResponse? {
                    if (!request.isForMainFrame || request.url.toString() != REPORT_URL) {
                        return null
                    }
                    return try {
                        val connection = URL(REPORT_URL).openConnection() as HttpURLConnection
                        connection.instanceFollowRedirects = true
                        connection.connectTimeout = 15_000
                        connection.readTimeout = 20_000
                        connection.setRequestProperty("Cache-Control", "no-cache")
                        connection.setRequestProperty("Pragma", "no-cache")
                        connection.connect()
                        if (connection.responseCode in 200..299) {
                            WebResourceResponse(
                                "text/html",
                                "UTF-8",
                                connection.inputStream
                            )
                        } else {
                            connection.disconnect()
                            null
                        }
                    } catch (_: Exception) {
                        null
                    }
                }

                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    return request.url.scheme != "https" ||
                        request.url.host != "raw.githubusercontent.com"
                }

                override fun onPageStarted(
                    view: WebView,
                    url: String,
                    favicon: android.graphics.Bitmap?
                ) {
                }

                override fun onPageFinished(view: WebView, url: String) {
                    swipeRefresh.isRefreshing = false
                    val royalCss = """
                        :root{--bg:#050505!important;--surface:#101010!important;--surface2:#17130b!important;--line:#4b3b19!important;--gold:#e7c56f!important;--text:#f7f2e5!important;--muted:#b4a98f!important;--green:#d8b66a!important;--red:#ff8b80!important}
                        html,body{background:#050505!important;color:#f7f2e5!important}
                        body{background:radial-gradient(ellipse at 85% -15%,#332710 0,transparent 38%),#050505!important}
                        .hero{background:linear-gradient(135deg,#17130b,#080808 72%)!important;border:1px solid #8b6b2b!important;box-shadow:0 18px 48px #000b,0 0 28px #d8b66a12!important}
                        .hero:after{border-color:#d8b66a55!important;box-shadow:0 0 0 24px #d8b66a18,0 0 0 48px #d8b66a0c!important}
                        .brand,h1,h2,.metric strong,.asset-price{color:#e7c56f!important}
                        .pill,nav,.metric,.panel,.asset-card,.risk-item,.chart-wrap{background:linear-gradient(145deg,#15120c,#080808)!important;border-color:#51401f!important}
                        nav{backdrop-filter:blur(14px)!important}
                        nav a:focus,nav a:hover{background:#3a2d15!important;color:#ffe3a0!important}
                        .paper-warning{background:#211a0d!important;border-color:#9b7835!important;color:#f1dfb3!important}
                        th{background:#211a0d!important;color:#d9c58e!important}
                        td,th{border-color:#302718!important}
                        tbody tr:hover{background:#d8b66a0c!important}
                        .buy{background:#332914!important;color:#f1d58d!important}
                        .sell{background:#421d1a!important;color:#ffaaa3!important}
                        .hold{background:#302817!important;color:#e8d69c!important}
                        .chart-wrap{background:#090909!important}
                        footer{color:#a99a75!important}
                        a{color:#e7c56f!important}
                    """.trimIndent()
                    val script = "(function(){var s=document.getElementById('mumin-royal-theme');if(!s){s=document.createElement('style');s.id='mumin-royal-theme';document.head.appendChild(s);}s.textContent=" + org.json.JSONObject.quote(royalCss) + ";})();"
                    view.evaluateJavascript(script, null)
                }

                override fun onReceivedError(
                    view: WebView,
                    request: WebResourceRequest,
                    error: WebResourceError
                ) {
                    if (request.isForMainFrame) {
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
        swipeRefresh.addView(webView, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
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

    @Suppress("DEPRECATION")
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
