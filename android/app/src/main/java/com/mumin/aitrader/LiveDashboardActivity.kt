package com.mumin.aitrader

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.content.FileProvider
import androidx.core.view.WindowCompat
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

class LiveDashboardActivity : Activity() {
    private lateinit var dashboard: WebView
    private lateinit var updateStatus: TextView
    private var pendingApk: File? = null

    companion object {
        private const val RELEASE_API = "https://api.github.com/repos/IskanderKhemiri1234/Mumin-AI-Trader/releases/latest"
        private const val USER_AGENT = "Mumin-AI-Trader-Android"
        private const val EMERALD = 0xFF071C17.toInt()
        private const val DASHBOARD_HTML = """
<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="color-scheme" content="dark"><title>Mumin AI Trader Live</title>
<style>
:root{color-scheme:dark;--bg:#06130f;--panel:#10251d;--line:#294b3b;--gold:#e7c982;--muted:#9eb5a8;--green:#65d6a0;--red:#ff928c}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -10%,#1b4934,transparent 45%),var(--bg);color:#f5f3e9;font:14px system-ui;padding:16px}
header,.card,.asset{background:linear-gradient(145deg,#142e23,#0b1c15);border:1px solid var(--line);border-radius:18px;padding:15px;margin-bottom:12px}
h1{font-size:19px;color:var(--gold);margin:0 0 5px}h2{font-size:15px;color:var(--gold);margin:0 0 12px}
.muted{color:var(--muted);font-size:11px}.status{display:flex;gap:8px;align-items:center}.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green)}
.metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.metric{padding:12px;border:1px solid var(--line);border-radius:13px;background:#081a13}.metric small{display:block;color:var(--muted)}.metric b{display:block;color:var(--gold);font-size:18px;margin-top:5px;overflow-wrap:anywhere}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.asset{margin:0;padding:11px}.top{display:flex;justify-content:space-between;align-items:center}.symbol{font-weight:800}.price{display:block;color:var(--gold);font-weight:700;margin-top:9px;font-variant-numeric:tabular-nums}.change{font-size:11px}.up{color:var(--green)}.down{color:var(--red)}svg{width:100%;height:36px;display:block;margin-top:5px}.rows{font-size:12px;overflow-x:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}td,th{text-align:right;padding:9px 6px;border-bottom:1px solid #294b3b}th{color:var(--muted)}.warning{color:#f1dfb3;border-color:#8b6b2b;background:#211a0d}.pill{display:inline-block;border:1px solid #8b6b2b;border-radius:20px;padding:4px 9px;color:var(--gold);font-size:10px}
@media(max-width:360px){body{padding:10px}.grid{grid-template-columns:1fr}.metric b{font-size:16px}}
</style></head><body>
<header><h1>MUMIN AI TRADER</h1><div class="status"><i class="dot"></i><span id="marketStatus" class="muted">الاتصال بالسوق العام…</span></div>
<p class="muted">أسعار السوق عبر WebSocket مباشر · سجل Paper يُحدّث كل 10 ثوانٍ</p><span class="pill">PAPER ONLY · LIVE ORDERS DISABLED</span></header>
<section class="card"><h2>المحفظة التجريبية · 70 USDT</h2><div class="metrics">
<div class="metric"><small>قيمة المحفظة</small><b id="equity">—</b></div><div class="metric"><small>النقد المتاح</small><b id="cash">—</b></div>
<div class="metric"><small>الربح المحقق</small><b id="realized">—</b></div><div class="metric"><small>العائد الكلي</small><b id="returns">—</b></div>
<div class="metric"><small>الصفقات المغلقة</small><b id="trades">—</b></div><div class="metric"><small>المراكز المفتوحة</small><b id="positionsCount">—</b></div></div><p id="paperUpdated" class="muted">بانتظار لقطة Paper موثقة…</p></section>
<section class="card"><h2>مؤشر الأصول الحي · Public Market</h2><div id="assets" class="grid"></div></section>
<section class="card"><h2>المراكز المفتوحة · Paper Ledger</h2><div id="positions" class="rows">تحميل…</div></section>
<section class="card"><h2>آخر الصفقات المسجلة</h2><div id="fills" class="rows">تحميل…</div></section>
<section class="card warning"><h2>حواجز السلامة</h2><div class="metrics"><div class="metric"><small>وقف الخسارة المحاكى</small><b>0.50%</b></div><div class="metric"><small>جني الربح المحاكى</small><b>1.25%</b></div><div class="metric"><small>حد الصفقة</small><b>5 USDT</b></div><div class="metric"><small>درع السبريد</small><b>8 bps</b></div></div><p class="muted">بيانات السوق عامة للعرض فقط. الرصيد والصفقات من سجل Paper فقط. GitHub Actions ليس خادماً دائماً؛ لذلك تحديث دفتر Paper مرتبط بنجاح الدورة المجدولة، وليس لحظياً.</p></section>
<script>
const symbols=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT'];
const history={}; let lastPaperTick=-1;
const fmt=(v,d=4)=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d}):'—'};
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function spark(values){if(values.length<2)return '';const lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;const pts=values.map((v,i)=>((i/(values.length-1))*180)+','+(30-((v-lo)/span)*25)).join(' ');return '<svg viewBox="0 0 180 36" preserveAspectRatio="none"><polyline fill="none" stroke="#e7c982" stroke-width="2" points="'+pts+'"/></svg>'}
function renderAssets(rows){document.getElementById('assets').innerHTML=symbols.map(s=>{const x=rows.find(v=>v.symbol===s);if(!x)return '<article class="asset"><b>'+s+'</b><span class="muted">لا توجد بيانات</span></article>';const p=Number(x.lastPrice),chg=Number(x.priceChangePercent);const h=history[s]||(history[s]=[]);h.push(p);if(h.length>24)h.shift();return '<article class="asset"><div class="top"><span class="symbol">'+s.replace('USDT','')+'</span><span class="change '+(chg>=0?'up':'down')+'">'+(chg>=0?'+':'')+fmt(chg,2)+'%</span></div><strong class="price">'+fmt(p,8)+' USDT</strong>'+spark(h)+'</article>'}).join('')}
const marketRows={};let marketSocket=null;let reconnectDelay=1000;let reconnectTimer=null;
function connectMarket(){
 if(marketSocket&&(marketSocket.readyState===WebSocket.OPEN||marketSocket.readyState===WebSocket.CONNECTING))return;
 const streams=symbols.map(s=>s.toLowerCase()+'@ticker').join('/');
 marketSocket=new WebSocket('wss://stream.binance.com:9443/stream?streams='+streams);
 marketSocket.onopen=()=>{reconnectDelay=1000;document.getElementById('marketStatus').textContent='WebSocket متصل · أسعار مباشرة';};
 marketSocket.onmessage=(event)=>{try{const packet=JSON.parse(event.data);const x=packet.data||packet;if(!x.s||!symbols.includes(x.s))return;marketRows[x.s]={symbol:x.s,lastPrice:x.c,priceChangePercent:x.P};renderAssets(Object.values(marketRows));document.getElementById('marketStatus').textContent='تحديث مباشر · '+new Date().toLocaleTimeString();}catch(e){document.getElementById('marketStatus').textContent='وصلت رسالة سوق غير صالحة';}};
 marketSocket.onerror=()=>{document.getElementById('marketStatus').textContent='خلل WebSocket · محاولة إعادة الاتصال';};
 marketSocket.onclose=()=>{document.getElementById('marketStatus').textContent='انقطع بث السوق · إعادة الاتصال تلقائياً';if(reconnectTimer)clearTimeout(reconnectTimer);reconnectTimer=setTimeout(connectMarket,reconnectDelay);reconnectDelay=Math.min(reconnectDelay*2,30000);};
}
function table(rows,heads,render){if(!rows||!rows.length)return '<p class="muted">لا توجد سجلات حالياً</p>';return '<table><thead><tr>'+heads.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(render).join('')+'</tbody></table>'}
async function paper(){try{const r=await fetch('latest-state.json?ts='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const root=await r.json();if(root.mode!=='PAPER_ONLY'||root.initial_capital_usdt!=='70.00')throw Error('Paper safety schema mismatch');const s=root.snapshot;if(Number(s.tick)<lastPaperTick)return;lastPaperTick=Number(s.tick);
document.getElementById('equity').textContent=fmt(s.equity)+' USDT';document.getElementById('cash').textContent=fmt(s.cash)+' USDT';document.getElementById('realized').textContent=fmt(s.realized_pnl)+' USDT';document.getElementById('returns').textContent=fmt(s.return_pct,3)+'%';document.getElementById('trades').textContent=s.trades;document.getElementById('positionsCount').textContent=s.positions.length;
document.getElementById('paperUpdated').textContent='الدورة #'+s.tick+' · آخر سجل: '+s.updated_at+' UTC · '+(s.halted?'إيقاف حماية':'Paper يعمل');
document.getElementById('positions').innerHTML=table(s.positions,['الأصل','الكمية','الدخول','السعر المرجعي','القيمة'],x=>'<tr><td>'+esc(x.symbol)+'</td><td>'+fmt(x.quantity,8)+'</td><td>'+fmt(x.entry_price,8)+'</td><td>'+fmt(x.mark_price,8)+'</td><td>'+fmt(x.market_value)+'</td></tr>');
document.getElementById('fills').innerHTML=table(s.fills,['الدورة','الأصل','الجانب','السعر','صافي النتيجة'],x=>'<tr><td>'+esc(x.tick)+'</td><td>'+esc(x.symbol)+'</td><td>'+esc(x.side)+'</td><td>'+fmt(x.fill_price,8)+'</td><td>'+fmt(x.net_pnl)+'</td></tr>');
}catch(e){document.getElementById('paperUpdated').textContent='تعذر تحديث سجل Paper: '+e.message+' · نحتفظ بآخر لقطة صحيحة.'}}
connectMarket();paper();setInterval(paper,10000);
</script></body></html>
"""
    }

    @Suppress("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.statusBarColor = EMERALD
        window.navigationBarColor = EMERALD
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(EMERALD)
        }
        val updatePanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(8))
            setBackgroundColor(0xFF10251D.toInt())
        }
        val updateTitle = TextView(this).apply {
            text = "Smart Update Center"
            textSize = 15f
            setTextColor(0xFFE7C982.toInt())
        }
        val updateButton = Button(this).apply {
            text = "Check for Updates"
            setOnClickListener { checkForUpdates() }
        }
        updateStatus = TextView(this).apply {
            text = "Updates are checked against official GitHub Releases."
            textSize = 11f
            setTextColor(0xFFB7C8BE.toInt())
        }
        updatePanel.addView(updateTitle)
        updatePanel.addView(updateButton)
        updatePanel.addView(updateStatus)
        root.addView(updatePanel, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ))

        dashboard = WebView(this).apply {
            setBackgroundColor(EMERALD)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.setSupportMultipleWindows(false)
            webViewClient = WebViewClient()
            webChromeClient = WebChromeClient()
        }
        root.addView(dashboard, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ))
        setContentView(root)
        dashboard.loadDataWithBaseURL(
            "https://raw.githubusercontent.com/IskanderKhemiri1234/Mumin-AI-Trader/mumin-paper-ledger/",
            DASHBOARD_HTML,
            "text/html",
            "UTF-8",
            null
        )
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun checkForUpdates() {
        updateStatus.text = "Checking GitHub Releases…"
        Thread {
            try {
                val connection = (URL(RELEASE_API).openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 15000
                    readTimeout = 20000
                    setRequestProperty("Accept", "application/vnd.github+json")
                    setRequestProperty("User-Agent", USER_AGENT)
                }
                val releaseText = connection.inputStream.bufferedReader().use { it.readText() }
                connection.disconnect()
                val release = JSONObject(releaseText)
                val tag = release.optString("tag_name").removePrefix("v")
                val current = packageManager.getPackageInfo(packageName, 0).versionName.orEmpty()
                val assetList = release.optJSONArray("assets")
                var apkUrl: String? = null
                if (assetList != null) {
                    for (index in 0 until assetList.length()) {
                        val asset = assetList.getJSONObject(index)
                        if (asset.optString("name").endsWith(".apk", ignoreCase = true)) {
                            apkUrl = asset.optString("browser_download_url")
                            break
                        }
                    }
                }
                if (tag.isBlank() || apkUrl.isNullOrBlank() || compareVersions(tag, current) <= 0) {
                    runOnUiThread {
                        updateStatus.text = if (apkUrl.isNullOrBlank()) {
                            "No APK asset found in the latest GitHub Release."
                        } else {
                            "You are using the latest version ($current)."
                        }
                    }
                } else {
                    runOnUiThread { updateStatus.text = "Version $tag found. Downloading…" }
                    val download = (URL(apkUrl).openConnection() as HttpURLConnection).apply {
                        connectTimeout = 20000
                        readTimeout = 30000
                        setRequestProperty("User-Agent", USER_AGENT)
                    }
                    val targetDir = getExternalFilesDir(android.os.Environment.DIRECTORY_DOWNLOADS)
                        ?: throw IllegalStateException("App download directory is unavailable.")
                    if (!targetDir.exists()) targetDir.mkdirs()
                    val apkFile = File(targetDir, "Mumin-AI-Trader-$tag.apk")
                    download.inputStream.use { input ->
                        FileOutputStream(apkFile).use { output -> input.copyTo(output) }
                    }
                    download.disconnect()
                    if (!apkFile.isFile || apkFile.length() < 1024) {
                        throw IllegalStateException("Downloaded APK is empty or incomplete.")
                    }
                    pendingApk = apkFile
                    runOnUiThread {
                        updateStatus.text = "Download complete. Opening Android installer…"
                        installDownloadedApk(apkFile)
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    updateStatus.text = "Update check failed: ${error.message ?: "unknown error"}"
                }
            }
        }.start()
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

    private fun installDownloadedApk(apkFile: File) {
        if (android.os.Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            updateStatus.text = "Allow installs from this app, then return to continue."
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")
                )
            )
            return
        }
        val apkUri = FileProvider.getUriForFile(this, "$packageName.fileprovider", apkFile)
        val installer = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(apkUri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        pendingApk = null
        startActivity(installer)
    }

    override fun onResume() {
        super.onResume()
        val apk = pendingApk ?: return
        if (apk.exists() &&
            (android.os.Build.VERSION.SDK_INT < 26 || packageManager.canRequestPackageInstalls())
        ) {
            pendingApk = null
            installDownloadedApk(apk)
        }
    }

    override fun onDestroy() {
        if (::dashboard.isInitialized) {
            dashboard.stopLoading()
            dashboard.destroy()
        }
        super.onDestroy()
    }
}
