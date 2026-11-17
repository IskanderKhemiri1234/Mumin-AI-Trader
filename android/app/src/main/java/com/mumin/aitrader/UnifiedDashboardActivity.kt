package com.mumin.aitrader

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.FileProvider
import androidx.core.view.WindowCompat
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class UnifiedDashboardActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var serverAddress: EditText
    private lateinit var connectionStatus: TextView
    private val io = Executors.newSingleThreadExecutor()
    private var pendingInstall: File? = null
    private var remoteHost: String? = null
    private var remotePort: Int = 8000
    private var remoteScheme: String? = null

    companion object {
        private const val PREFS = "mumin_dashboard_connection"
        private const val SERVER_KEY = "server_base_url"
        private const val RELEASE_API =
            "https://api.github.com/repos/IskanderKhemiri1234/Mumin-AI-Trader/releases/latest"
        private const val USER_AGENT = "Mumin-AI-Trader-Android"
        private const val INSTALL_REQUEST_CODE = 7102
        private const val EMERALD = 0xFF071C17.toInt()
        private const val GOLD = 0xFFD8B66A.toInt()
        private const val LIGHT = 0xFFF4F0E6.toInt()
        private const val MUTED = 0xFFB7C8BE.toInt()
    }

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
            setPadding(dp(16), dp(12), dp(16), dp(10))
            setBackgroundColor(EMERALD)
        }
        header.addView(TextView(this).apply {
            text = "MUMIN AI TRADER"
            textSize = 18f
            setTextColor(GOLD)
            setTypeface(null, android.graphics.Typeface.BOLD)
        })
        header.addView(TextView(this).apply {
            text = "لوحة تشغيل موحّدة · Unified Operations Center"
            textSize = 11f
            setTextColor(MUTED)
            setPadding(0, dp(3), 0, dp(8))
        })
        val connectionPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setBackgroundColor(0xFF10251D.toInt())
        }
        serverAddress = EditText(this).apply {
            hint = "عنوان الخادم المحلي أو رابط HTTPS"
            setSingleLine(true)
            textSize = 13f
            setTextColor(LIGHT)
            setHintTextColor(MUTED)
            setSelectAllOnFocus(false)
            setText(getPreferences(MODE_PRIVATE).getString(SERVER_KEY, "").orEmpty())
        }
        connectionPanel.addView(serverAddress, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))
        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        actions.addView(Button(this).apply {
            text = "اتصال بالخادم"
            setOnClickListener { connectToDashboard() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        actions.addView(Button(this).apply {
            text = "تحديث التطبيق"
            setOnClickListener { checkForUpdates() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        actions.addView(Button(this).apply {
            text = "العرض التجريبي المحلي"
            setOnClickListener { loadLocalDemo() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        connectionPanel.addView(actions)
        connectionStatus = TextView(this).apply {
            text = "الوضع الحالي: عرض تجريبي محلي دون خادم. البيانات اصطناعية وليست من Binance."
            textSize = 11f
            setTextColor(MUTED)
            setPadding(0, dp(6), 0, 0)
        }
        connectionPanel.addView(connectionStatus)
        connectionPanel.visibility = View.GONE
        header.addView(Button(this).apply {
            text = "اتصال بالخادم (اختياري)"
            setOnClickListener {
                connectionPanel.visibility =
                    if (connectionPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE
            }
        }, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))
        root.addView(header)
        root.addView(connectionPanel)

        webView = WebView(this).apply {
            setBackgroundColor(EMERALD)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowFileAccessFromFileURLs = false
            settings.allowUniversalAccessFromFileURLs = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.setSupportMultipleWindows(false)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                settings.safeBrowsingEnabled = true
            }
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean {
                    val target = request.url
                    if (target.scheme == "file") {
                        return !target.path.orEmpty().startsWith("/android_asset/dashboard/")
                    }
                    val expectedScheme = remoteScheme ?: return true
                    val expectedHost = remoteHost ?: return true
                    val targetPort = target.port.takeIf { it > 0 } ?: if (expectedScheme == "https") 443 else 80
                    return target.scheme != expectedScheme ||
                        !target.host.equals(expectedHost, ignoreCase = true) ||
                        targetPort != remotePort
                }

                override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                    connectionStatus.text = if (remoteHost == null) "جارٍ فتح العرض التجريبي المحلي…" else "جارٍ فتح لوحة الويب من الخادم…"
                }

                override fun onPageFinished(view: WebView, url: String) {
                    connectionStatus.text = if (remoteHost == null) "العرض التجريبي المحلي جاهز · بيانات اصطناعية · لا اتصال بـ Binance." else "تم تحميل لوحة التحكم من الخادم. أدخل رمز لوحة التحكم داخل الصفحة."
                }

                override fun onReceivedError(
                    view: WebView,
                    request: WebResourceRequest,
                    error: WebResourceError
                ) {
                    if (request.isForMainFrame) {
                        connectionStatus.text =
                            "تعذر الوصول إلى الخادم. تحقق من العنوان وحالة الخدمة ثم أعد الاتصال."
                    }
                }
            }
            webChromeClient = WebChromeClient()
        }
        root.addView(webView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ))
        setContentView(root)

        val saved = getSavedServerUrl()
        val initialServer = saved.ifBlank { "http://127.0.0.1:8000" }
        serverAddress.setText(initialServer)
        connectToDashboard()
    }

    private fun connectToDashboard() {
        val normalized = normalizeServerUrl(serverAddress.text.toString())
        if (normalized == null) {
            connectionStatus.text = "العنوان غير صالح. استخدم HTTPS دون مسار أو بيانات اعتماد."
            return
        }
        getPreferences(MODE_PRIVATE).edit().putString(SERVER_KEY, normalized).apply()
        remoteHost = Uri.parse(normalized).host
        remoteScheme = Uri.parse(normalized).scheme
        remotePort = Uri.parse(normalized).port.takeIf { it > 0 }
            ?: if (remoteScheme == "https") 443 else 8000
        loadUnifiedDashboard(normalized)
    }

    private fun normalizeServerUrl(value: String): String? {
        val trimmed = value.trim().trimEnd('/')
        if (trimmed.isBlank()) return null
        val uri = runCatching { Uri.parse(trimmed) }.getOrNull() ?: return null
        val host = uri.host?.lowercase().orEmpty()
        val localHttp = uri.scheme == "http" && host in setOf("127.0.0.1", "localhost")
        val remoteHttps = uri.scheme == "https" && host.isNotBlank()
        if (!localHttp && !remoteHttps) return null
        if (uri.userInfo != null || uri.query != null || uri.fragment != null) return null
        if (remoteHttps && uri.port != -1 && uri.port != 443) return null
        if (localHttp && uri.port != -1 && uri.port != 8000) return null
        val path = uri.path.orEmpty()
        if (path.isNotBlank() && path != "/") return null
        return if (localHttp) "http://$host:8000" else "https://$host"
    }

    private fun getSavedServerUrl(): String =
        getPreferences(MODE_PRIVATE).getString(SERVER_KEY, "").orEmpty()

    private fun loadLocalDemo() {
        remoteHost = null
        remotePort = 443
        remoteScheme = null
        webView.visibility = View.VISIBLE
        connectionStatus.text = "العرض التجريبي المحلي · بيانات اصطناعية فقط · لا اتصال بخادم أو Binance."
        runCatching {
            assets.open("dashboard/index.html").bufferedReader(Charsets.UTF_8).use { it.readText() }
        }.onSuccess { html ->
            webView.loadDataWithBaseURL(
                "https://appassets.androidplatform.net/assets/dashboard/index.html",
                html,
                "text/html",
                "UTF-8",
                null
            )
        }.onFailure {
            connectionStatus.text = "تعذر تحميل لوحة العرض المحلية المضمّنة."
        }
    }

    private fun loadUnifiedDashboard(baseUrl: String) {
        val uri = Uri.parse(baseUrl)
        val host = uri.host?.lowercase().orEmpty()
        val localHttp = uri.scheme == "http" && host in setOf("127.0.0.1", "localhost") && (uri.port == -1 || uri.port == 8000)
        val remoteHttps = uri.scheme == "https" && host.isNotBlank() && (uri.port == -1 || uri.port == 443)
        if (!localHttp && !remoteHttps) {
            connectionStatus.text = "اتصال مرفوض: استخدم خادم الهاتف المحلي أو HTTPS موثوقاً."
            return
        }
        remoteHost = host
        remoteScheme = uri.scheme
        remotePort = uri.port.takeIf { it > 0 } ?: if (localHttp) 8000 else 443
        webView.visibility = View.VISIBLE
        connectionStatus.text = if (remoteScheme == "http") "الاتصال بخادم Termux المحلي… أدخل رمز لوحة التحكم عند الطلب." else "جارٍ الاتصال بالخادم الآمن…"
        webView.loadUrl("$baseUrl/dashboard", mapOf("Cache-Control" to "no-cache"))
    }

    private fun checkForUpdates() {
        connectionStatus.text = "جارٍ التحقق من إصدارات التطبيق…"
        io.execute {
            try {
                val connection = (URL(RELEASE_API).openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 15000
                    readTimeout = 20000
                    setRequestProperty("Accept", "application/vnd.github+json")
                    setRequestProperty("User-Agent", USER_AGENT)
                }
                val release = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                connection.disconnect()
                val tag = release.optString("tag_name").removePrefix("v")
                val current = packageManager.getPackageInfo(packageName, 0).versionName.orEmpty()
                val assets = release.optJSONArray("assets")
                var apkUrl: String? = null
                if (assets != null) {
                    for (index in 0 until assets.length()) {
                        val asset = assets.getJSONObject(index)
                        if (asset.optString("name").endsWith(".apk", ignoreCase = true)) {
                            apkUrl = asset.optString("browser_download_url")
                            break
                        }
                    }
                }
                if (tag.isBlank() || apkUrl.isNullOrBlank() || compareVersions(tag, current) <= 0) {
                    runOnUiThread {
                        connectionStatus.text = if (apkUrl.isNullOrBlank()) {
                            "لا يوجد ملف APK في أحدث إصدار منشور."
                        } else {
                            "التطبيق محدّث بالفعل · $current"
                        }
                    }
                    return@execute
                }
                val downloadUrl = apkUrl
                if (!downloadUrl.startsWith("https://github.com/")) {
                    throw IllegalStateException("Release asset URL is not an approved GitHub HTTPS URL.")
                }
                runOnUiThread { connectionStatus.text = "الإصدار $tag متاح. جارٍ التنزيل…" }
                val download = (URL(downloadUrl).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 20000
                    readTimeout = 30000
                    setRequestProperty("User-Agent", USER_AGENT)
                }
                val directory = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                    ?: throw IllegalStateException("App download directory is unavailable.")
                if (!directory.exists()) directory.mkdirs()
                val apk = File(directory, "Mumin-AI-Trader-$tag.apk")
                download.inputStream.use { input -> apk.outputStream().use { output -> input.copyTo(output) } }
                download.disconnect()
                if (!apk.isFile || apk.length() < 1024) {
                    throw IllegalStateException("Downloaded APK is incomplete.")
                }
                val archiveInfo = packageManager.getPackageArchiveInfo(apk.absolutePath, 0)
                if (archiveInfo?.packageName != packageName) {
                    apk.delete()
                    throw IllegalStateException("Downloaded APK package does not match this application.")
                }
                pendingInstall = apk
                runOnUiThread {
                    connectionStatus.text = "اكتمل التنزيل. جارٍ فتح مثبت Android…"
                    installDownloadedApk(apk)
                }
            } catch (error: Exception) {
                runOnUiThread {
                    connectionStatus.text = "تعذر التحقق من التحديث: ${error.message ?: "unknown error"}"
                }
            }
        }
    }

    private fun compareVersions(first: String, second: String): Int {
        val left = first.split(".", "-", "+").map { it.toIntOrNull() ?: 0 }
        val right = second.split(".", "-", "+").map { it.toIntOrNull() ?: 0 }
        for (index in 0 until maxOf(left.size, right.size)) {
            val a = left.getOrElse(index) { 0 }
            val b = right.getOrElse(index) { 0 }
            if (a != b) return a.compareTo(b)
        }
        return 0
    }

    private fun installDownloadedApk(apk: File) {
        if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            connectionStatus.text = "اسمح بالتثبيت من هذا التطبيق ثم ارجع لإكمال التحديث."
            startActivityForResult(Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName")
            ), INSTALL_REQUEST_CODE)
            return
        }
        val apkUri = FileProvider.getUriForFile(this, "$packageName.fileprovider", apk)
        startActivity(Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(apkUri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    }

    @Deprecated("Deprecated by Android; retained for package installer permission result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (
            requestCode == INSTALL_REQUEST_CODE &&
            Build.VERSION.SDK_INT >= 26 &&
            packageManager.canRequestPackageInstalls()
        ) {
            pendingInstall?.let { installDownloadedApk(it) }
        }
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    override fun onDestroy() {
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.destroy()
        }
        io.shutdownNow()
        super.onDestroy()
    }
}
