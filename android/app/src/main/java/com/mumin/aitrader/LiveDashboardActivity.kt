package com.mumin.aitrader

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.view.WindowCompat

class LiveDashboardActivity : Activity() {
    private lateinit var dashboard: WebView

    companion object {
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
<p class="muted">مؤشر السوق يتحدث كل 5 ثوانٍ · سجل Paper يتحدث عند نشر دورة المحرك</p><span class="pill">PAPER ONLY · LIVE ORDERS DISABLED</span></header>
<section class="card"><h2>المحفظة التجريبية · 70 USDT</h2><div class="metrics">
<div class="metric"><small>قيمة المحفظة</small><b id="equity">—</b></div><div class="metric"><small>النقد المتاح</small><b id="cash">—</b></div>
<div class="metric"><small>الربح المحقق</small><b id="realized">—</b></div><div class="metric"><small>العائد الكلي</small><b id="returns">—</b></div>
<div class="metric"><small>الصفقات المغلقة</small><b id="trades">—</b></div><div class="metric"><small>المراكز المفتوحة</small><b id="positionsCount">—</b></div></div><p id="paperUpdated" class="muted">بانتظار لقطة Paper موثقة…</p></section>
<section class="card"><h2>مؤشر الأصول الحي · Public Market</h2><div id="assets" class="grid"></div></section>
<section class="card"><h2>المراكز المفتوحة · Paper Ledger</h2><div id="positions" class="rows">تحميل…</div></section>
<section class="card"><h2>آخر الصفقات المسجلة</h2><div id="fills" class="rows">تحميل…</div></section>
<section class="card warning"><h2>حواجز السلامة</h2><div class="metrics"><div class="metric"><small>وقف الخسارة المحاكى</small><b>0.50%</b></div><div class="metric"><small>جني الربح المحاكى</small><b>1.25%</b></div><div class="metric"><small>حد الصفقة</small><b>5 USDT</b></div><div class="metric"><small>درع السبريد</small><b>12 bps</b></div></div><p class="muted">بيانات السوق عامة للعرض فقط. الرصيد والصفقات من سجل Paper فقط. GitHub Actions ليس خادماً دائماً؛ لذلك تحديث دفتر Paper مرتبط بنجاح الدورة المجدولة، وليس لحظياً.</p></section>
<script>
const symbols=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','LINKUSDT'];
const history={}; let lastPaperTick=-1;
const fmt=(v,d=4)=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d}):'—'};
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function spark(values){if(values.length<2)return '';const lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;const pts=values.map((v,i)=>((i/(values.length-1))*180)+','+(30-((v-lo)/span)*25)).join(' ');return '<svg viewBox="0 0 180 36" preserveAspectRatio="none"><polyline fill="none" stroke="#e7c982" stroke-width="2" points="'+pts+'"/></svg>'}
function renderAssets(rows){document.getElementById('assets').innerHTML=symbols.map(s=>{const x=rows.find(v=>v.symbol===s);if(!x)return '<article class="asset"><b>'+s+'</b><span class="muted">لا توجد بيانات</span></article>';const p=Number(x.lastPrice),chg=Number(x.priceChangePercent);const h=history[s]||(history[s]=[]);h.push(p);if(h.length>24)h.shift();return '<article class="asset"><div class="top"><span class="symbol">'+s.replace('USDT','')+'</span><span class="change '+(chg>=0?'up':'down')+'">'+(chg>=0?'+':'')+fmt(chg,2)+'%</span></div><strong class="price">'+fmt(p,8)+' USDT</strong>'+spark(h)+'</article>'}).join('')}
async function market(){try{const url='https://api.binance.com/api/v3/ticker/24hr?symbols='+encodeURIComponent(JSON.stringify(symbols));const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const rows=await r.json();renderAssets(rows);document.getElementById('marketStatus').textContent='السوق العام متصل · '+new Date().toLocaleTimeString();}catch(e){document.getElementById('marketStatus').textContent='تعذر تحديث أسعار السوق · '+e.message}}
function table(rows,heads,render){if(!rows||!rows.length)return '<p class="muted">لا توجد سجلات حالياً</p>';return '<table><thead><tr>'+heads.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(render).join('')+'</tbody></table>'}
async function paper(){try{const r=await fetch('latest-state.json?ts='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const root=await r.json();if(root.mode!=='PAPER_ONLY'||root.initial_capital_usdt!=='70.00')throw Error('Paper safety schema mismatch');const s=root.snapshot;if(Number(s.tick)<lastPaperTick)return;lastPaperTick=Number(s.tick);
document.getElementById('equity').textContent=fmt(s.equity)+' USDT';document.getElementById('cash').textContent=fmt(s.cash)+' USDT';document.getElementById('realized').textContent=fmt(s.realized_pnl)+' USDT';document.getElementById('returns').textContent=fmt(s.return_pct,3)+'%';document.getElementById('trades').textContent=s.trades;document.getElementById('positionsCount').textContent=s.positions.length;
document.getElementById('paperUpdated').textContent='الدورة #'+s.tick+' · آخر سجل: '+s.updated_at+' UTC · '+(s.halted?'إيقاف حماية':'Paper يعمل');
document.getElementById('positions').innerHTML=table(s.positions,['الأصل','الكمية','الدخول','السعر المرجعي','القيمة'],x=>'<tr><td>'+esc(x.symbol)+'</td><td>'+fmt(x.quantity,8)+'</td><td>'+fmt(x.entry_price,8)+'</td><td>'+fmt(x.mark_price,8)+'</td><td>'+fmt(x.market_value)+'</td></tr>');
document.getElementById('fills').innerHTML=table(s.fills,['الدورة','الأصل','الجانب','السعر','صافي النتيجة'],x=>'<tr><td>'+esc(x.tick)+'</td><td>'+esc(x.symbol)+'</td><td>'+esc(x.side)+'</td><td>'+fmt(x.fill_price,8)+'</td><td>'+fmt(x.net_pnl)+'</td></tr>');
}catch(e){document.getElementById('paperUpdated').textContent='تعذر تحديث سجل Paper: '+e.message+' · نحتفظ بآخر لقطة صحيحة.'}}
market();paper();setInterval(market,5000);setInterval(paper,10000);
</script></body></html>
"""
    }

    @Suppress("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.statusBarColor = EMERALD
        window.navigationBarColor = EMERALD
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
        setContentView(dashboard)
        dashboard.loadDataWithBaseURL(
            "https://raw.githubusercontent.com/IskanderKhemiri1234/Mumin-AI-Trader/mumin-paper-ledger/",
            DASHBOARD_HTML,
            "text/html",
            "UTF-8",
            null
        )
    }

    override fun onDestroy() {
        if (::dashboard.isInitialized) {
            dashboard.stopLoading()
            dashboard.destroy()
        }
        super.onDestroy()
    }
}
