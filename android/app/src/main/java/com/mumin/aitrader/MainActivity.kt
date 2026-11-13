package com.mumin.aitrader

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
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
import java.io.File
import java.util.concurrent.Executors
import org.json.JSONObject
import androidx.core.content.FileProvider

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private var updateDownloadId: Long = -1L
    private var pendingUpdateUri: Uri? = null
    private val updateExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private var updateReceiver: BroadcastReceiver? = null

    companion object {
        private const val LATEST_RELEASE_URL = "https://api.github.com/repos/IskanderKhemiri1234/Mumin-AI-Trader/releases/latest"
        private const val CURRENT_VERSION = "1.1.0"
        private const val INSTALL_REQUEST_CODE = 7102
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
            checkForUpdates()
        } else {
            webView.restoreState(savedInstanceState)
            if (webView.url.isNullOrBlank()) loadLatestReport()
        }
    }

    private fun checkForUpdates() {
        updateExecutor.execute {
            try {
                val connection = URL(LATEST_RELEASE_URL).openConnection() as HttpURLConnection
                connection.connectTimeout = 10_000
                connection.readTimeout = 10_000
                connection.setRequestProperty("Accept", "application/vnd.github+json")
                connection.setRequestProperty("User-Agent", "Mumin-AI-Trader-Android")
                val json = connection.inputStream.bufferedReader().use { it.readText() }
                connection.disconnect()
                val release = JSONObject(json)
                val latestVersion = release.optString("tag_name").removePrefix("v")
                val assets = release.optJSONArray("assets") ?: return@execute
                var apkUrl: String? = null
                for (index in 0 until assets.length()) {
                    val asset = assets.getJSONObject(index)
                    if (asset.optString("name").endsWith(".apk", ignoreCase = true)) {
                        apkUrl = asset.optString("browser_download_url")
                        break
                    }
                }
                if (apkUrl.isNullOrBlank() || !apkUrl.startsWith("https://github.com/")) return@execute
                val installedVersion = packageManager.getPackageInfo(packageName, 0).versionName ?: CURRENT_VERSION
                if (compareVersions(latestVersion, installedVersion) > 0) {
                    mainHandler.post {
                        AlertDialog.Builder(this)
                            .setTitle("تحديث جديد متاح")
                            .setMessage("يتوفر الإصدار $latestVersion من Mumin AI Trader. نزّله لتثبيت التحسينات والميزات الجديدة.")
                            .setNegativeButton("لاحقاً", null)
                            .setPositiveButton("تنزيل التحديث") { _, _ -> downloadUpdate(apkUrl) }
                            .show()
                    }
                }
            } catch (_: Exception) {
                // Update checks are best-effort; the report remains usable offline.
            }
        }
    }

    private fun compareVersions(first: String, second: String): Int {
        val a = first.split(".").map { it.toIntOrNull() ?: 0 }
        val b = second.split(".").map { it.toIntOrNull() ?: 0 }
        for (index in 0 until maxOf(a.size, b.size)) {
            val left = a.getOrElse(index) { 0 }
            val right = b.getOrElse(index) { 0 }
            if (left != right) return left.compareTo(right)
        }
        return 0
    }

    private fun downloadUpdate(url: String) {
        try {
            val request = DownloadManager.Request(Uri.parse(url))
                .setTitle("Mumin AI Trader update")
                .setDescription("Downloading the latest verified app release")
                .setMimeType("application/vnd.android.package-archive")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, "Mumin-AI-Trader-update.apk")
            val manager = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            updateDownloadId = manager.enqueue(request)
            registerUpdateReceiver()
        } catch (_: Exception) {
            AlertDialog.Builder(this)
                .setMessage("تعذر بدء تنزيل التحديث. تحقق من الاتصال وحاول مجدداً.")
                .setPositiveButton("حسناً", null)
                .show()
        }
    }

    private fun registerUpdateReceiver() {
        if (updateReceiver != null) return
        updateReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L) != updateDownloadId) return
                val manager = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                val query = DownloadManager.Query().setFilterById(updateDownloadId)
                manager.query(query).use { cursor ->
                    if (!cursor.moveToFirst()) return
                    val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    if (status != DownloadManager.STATUS_SUCCESSFUL) {
                        AlertDialog.Builder(this@MainActivity)
                            .setMessage("لم يكتمل تنزيل التحديث. حاول مرة أخرى.")
                            .setPositiveButton("حسناً", null)
                            .show()
                        return
                    }
                    pendingUpdateUri = manager.getUriForDownloadedFile(updateDownloadId)
                    installDownloadedUpdate()
                }
            }
        }
        val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(updateReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(updateReceiver, filter)
        }
    }

    private fun installDownloadedUpdate() {
        val uri = pendingUpdateUri ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            val settingsIntent = Intent(
                android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName")
            )
            startActivityForResult(settingsIntent, INSTALL_REQUEST_CODE)
            return
        }
        val installUri = FileProvider.getUriForFile(this, packageName + ".fileprovider", getDownloadedApkFile())
        val installIntent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(installUri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(installIntent)
    }

    private fun getDownloadedApkFile(): File {
        val directory = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: throw IllegalStateException("Download directory unavailable")
        return File(directory, "Mumin-AI-Trader-update.apk")
    }

    @Deprecated("Deprecated by Android; retained for Android 6+ package installer settings result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == INSTALL_REQUEST_CODE && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            packageManager.canRequestPackageInstalls()) {
            installDownloadedUpdate()
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
        updateReceiver?.let { try { unregisterReceiver(it) } catch (_: IllegalArgumentException) { } }
        updateReceiver = null
        updateExecutor.shutdownNow()
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.destroy()
        }
        super.onDestroy()
    }
}
