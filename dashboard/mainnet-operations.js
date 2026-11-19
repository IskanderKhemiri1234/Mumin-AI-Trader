(() => {
"use strict";
const nav=document.querySelector(".nav"),main=document.querySelector("main.main");
if(!nav||!main||document.getElementById("mainnetOperationsPage"))return;
const style=document.createElement("style");
style.textContent=`
.mainnet-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0}
.mainnet-panel{min-width:0;padding:15px;border:1px solid var(--line);border-radius:15px;background:linear-gradient(145deg,#14271c,#0b1711)}
.mainnet-panel h3{font-size:12px;margin:0 0 10px;color:var(--gold2)}
.mainnet-kv{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid #263a2e;font-size:10px}
.mainnet-kv span{color:var(--muted)}.mainnet-kv b{overflow-wrap:anywhere;text-align:left}
.mainnet-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
.mainnet-form label{display:grid;gap:5px;color:var(--muted);font-size:10px}
.mainnet-form input,.mainnet-form select{width:100%;min-width:0;padding:10px;border:1px solid var(--line2);border-radius:9px;background:#07130e;color:var(--text)}
.mainnet-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.mainnet-warning{padding:11px;border:1px solid #94774166;border-radius:11px;background:#30291b55;color:#e3d5af;font-size:10px;line-height:1.7;margin:10px 0}
.mainnet-table{overflow:auto}.mainnet-table table{min-width:620px}
.mainnet-status{display:inline-flex;padding:5px 9px;border:1px solid #94774166;border-radius:99px;color:var(--gold2);font-size:10px}
.mainnet-status.ok{color:var(--green);border-color:#315b48}.mainnet-status.bad{color:var(--red);border-color:#8a4943}
@media(max-width:760px){.mainnet-grid{grid-template-columns:1fr}.mainnet-form{grid-template-columns:1fr}}
`;
document.head.appendChild(style);
const navButton=document.createElement("button");
navButton.type="button";navButton.dataset.page="mainnetOperationsPage";
navButton.innerHTML='<span class="ico">◉</span><span>حساب Binance</span>';
nav.appendChild(navButton);
const page=document.createElement("section");
page.id="mainnetOperationsPage";page.className="page";
page.innerHTML=`
<div class="paper-section-heading"><div><div class="eyebrow">BINANCE SPOT · MAINNET</div><h2>الحساب الحقيقي والتنفيذ</h2><p>قراءة حساب Binance، المصالحة، سجل الأوامر والصفقات.</p></div><span id="mainnetState" class="mainnet-status">جارٍ الفحص</span></div>
<div class="mainnet-warning">التداول الحقيقي يستخدم أموال الحساب الفعلية. هذه الصفحة لا تفتح Mainnet من تلقاء نفسها؛ إذا كانت بوابات الخادم مغلقة فستبقى جميع أوامر التنفيذ مقفلة. لا تستخدم مفاتيح السحب أو الهامش أو العقود الآجلة.</div>
<div class="mainnet-actions"><button class="btn" id="mainnetRefresh">تحديث الحساب</button><button class="btn" id="mainnetReconcile">مصالحة كاملة</button><button class="btn" id="mainnetStop">إيقاف طوارئ</button></div>
<div id="mainnetMessage" class="page-intro" aria-live="polite"></div>
<div class="mainnet-grid">
<section class="mainnet-panel"><h3>حالة التنفيذ</h3><div id="mainnetStatusDetails"></div></section>
<section class="mainnet-panel"><h3>حساب Binance Spot</h3><div id="mainnetAccountDetails"></div></section>
<section class="mainnet-panel"><h3>الأداء والدفتر</h3><div id="mainnetPnlDetails"></div></section>
</div>
<section class="mainnet-panel"><h3>إرسال أمر LIMIT — يتطلب تأكيداً صريحاً</h3>
<form id="mainnetOrderForm" class="mainnet-form">
<label>العملة<select id="mainnetSymbol"><option>BTCUSDT</option><option>ETHUSDT</option><option>BNBUSDT</option><option>SOLUSDT</option><option>XRPUSDT</option><option>ADAUSDT</option><option>AVAXUSDT</option><option>LINKUSDT</option></select></label>
<label>الاتجاه<select id="mainnetSide"><option>BUY</option><option>SELL</option></select></label>
<label>الكمية<input id="mainnetQuantity" inputmode="decimal" type="number" min="0" step="any" required></label>
<label>السعر المحدد<input id="mainnetPrice" inputmode="decimal" type="number" min="0" step="any" required></label>
<label id="mainnetTakeProfitWrap">سعر جني الأرباح<input id="mainnetTakeProfit" inputmode="decimal" type="number" min="0" step="any"></label>
<label id="mainnetStopWrap">سعر وقف الخسارة<input id="mainnetStop" inputmode="decimal" type="number" min="0" step="any"></label>
<label id="mainnetStopLimitWrap">سعر حد وقف الخسارة<input id="mainnetStopLimit" inputmode="decimal" type="number" min="0" step="any"></label>
<div class="mainnet-actions"><button class="btn gold" id="mainnetSubmit" type="submit" disabled>تأكيد وإرسال LIMIT</button></div>
</form></section>
<div class="mainnet-grid">
<section class="mainnet-panel"><h3>الأرصدة</h3><div id="mainnetBalances" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>الأوامر</h3><div id="mainnetOrders" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>الصفقات الأخيرة</h3><div id="mainnetTrades" class="mainnet-table"></div></section>
</div>`;
const footer=main.querySelector(".footer");if(footer)main.insertBefore(page,footer);else main.appendChild(page);
const $=id=>document.getElementById(id);
let busy=false,enabled=false;
const password=()=>sessionStorage.getItem("mumin_dashboard_password");
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path,options={}){const token=password();if(!token)throw new Error("يجب تسجيل الدخول إلى اللوحة أولاً.");const response=await fetch(path,{...options,headers:{"X-Dashboard-Password":token,...(options.headers||{})},cache:"no-store"});let payload={};try{payload=await response.json()}catch{}if(!response.ok)throw new Error(payload.detail||("HTTP "+response.status));return payload}
function setMessage(value,bad=false){$("mainnetMessage").textContent=value;$("mainnetMessage").style.color=bad?"var(--red)":"var(--muted)"}
function kv(label,value){return '<div class="mainnet-kv"><span>'+escapeHtml(label)+'</span><b>'+escapeHtml(value)+'</b></div>'}
function table(headers,rows){return '<table><thead><tr>'+headers.map(h=>'<th>'+escapeHtml(h)+'</th>').join('')+'</tr></thead><tbody>'+(rows.length?rows.join(''):'<tr><td colspan="'+headers.length+'" class="empty">لا توجد بيانات</td></tr>')+'</tbody></table>'}
function openPage(){document.querySelectorAll(".page").forEach(x=>x.classList.toggle("active",x.id===page.id));document.querySelectorAll("[data-page]").forEach(x=>x.classList.toggle("active",x.dataset.page===page.id));$("crumbText").textContent="حساب Binance";load()}
navButton.addEventListener("click",openPage);
async function load(){if(busy)return;busy=true;$("mainnetRefresh").disabled=true;try{
 const status=await api("/api/mainnet/status");enabled=!!status.execution_enabled;
 $("mainnetState").textContent=status.status||"UNKNOWN";$("mainnetState").className="mainnet-status "+(enabled&&!status.halted?"ok":"bad");
 $("mainnetStatusDetails").innerHTML=kv("Environment",status.environment||"—")+kv("Mode",status.mode||"—")+kv("Host",status.host||"—")+kv("Unresolved orders",status.unresolved_orders??"—")+kv("Halt reason",status.halt_reason||"—");
 $("mainnetSubmit").disabled=!enabled||!!status.halted;
 if(!enabled){$("mainnetAccountDetails").innerHTML=kv("الحالة","مقفول من إعدادات الخادم");$("mainnetPnlDetails").innerHTML=kv("التداول الحقيقي","غير مفعّل");$("mainnetBalances").innerHTML="—";$("mainnetOrders").innerHTML="—";$("mainnetTrades").innerHTML="—";setMessage("Mainnet مقفول حالياً. لا يمكن إرسال أي أمر.");return}
 const [account,pnl,orders,trades]=await Promise.all([api("/api/mainnet/account"),api("/api/mainnet/pnl"),api("/api/mainnet/orders?limit=20"),api("/api/mainnet/trades?limit=20")]);
 $("mainnetAccountDetails").innerHTML=kv("Account type",account.account_type)+kv("Spot trading permission",String(account.can_trade));
 $("mainnetPnlDetails").innerHTML=kv("Realized PnL",pnl.realized_pnl??pnl.realized??"—")+kv("Ledger complete",String(pnl.complete))+kv("Unresolved items",(pnl.unresolved||[]).length);
 $("mainnetBalances").innerHTML=table(["Asset","Free","Locked"],(account.balances||[]).filter(x=>Number(x.free||0)+Number(x.locked||0)>0).map(x=>'<tr><td>'+escapeHtml(x.asset)+'</td><td>'+escapeHtml(x.free)+'</td><td>'+escapeHtml(x.locked)+'</td></tr>'));
 $("mainnetOrders").innerHTML=table(["Symbol","Side","Qty","Price","State"],(orders.orders||[]).map(x=>'<tr><td>'+escapeHtml(x.symbol)+'</td><td>'+escapeHtml(x.side)+'</td><td>'+escapeHtml(x.quantity)+'</td><td>'+escapeHtml(x.price)+'</td><td>'+escapeHtml(x.state)+'</td></tr>'));
 $("mainnetTrades").innerHTML=table(["Symbol","Side","Qty","Price","Fee"],(trades.trades||[]).slice(0,20).map(x=>'<tr><td>'+escapeHtml(x.symbol)+'</td><td>'+((x.is_buyer)?"BUY":"SELL")+'</td><td>'+escapeHtml(x.quantity)+'</td><td>'+escapeHtml(x.price)+'</td><td>'+escapeHtml(x.commission)+" "+escapeHtml(x.commission_asset)+'</td></tr>'));
 setMessage("تم تحديث بيانات حساب Binance Mainnet من الخادم.");
 }catch(error){setMessage(error.message,true);$("mainnetState").textContent="ERROR";$("mainnetState").className="mainnet-status bad"}finally{busy=false;$("mainnetRefresh").disabled=false}}
$("mainnetRefresh").addEventListener("click",load);
$("mainnetReconcile").addEventListener("click",async()=>{if(!window.confirm("تنفيذ مصالحة كاملة للأرصدة والطلبات والصفقات؟"))return;try{const result=await api("/api/mainnet/reconcile",{method:"POST"});setMessage("اكتملت المصالحة. الاختلافات: "+(result.discrepancies||[]).length);await load()}catch(error){setMessage(error.message,true)}});
$("mainnetStop").addEventListener("click",async()=>{if(!window.confirm("تأكيد إيقاف Mainnet؟ سيوقف الإدخالات ويلغي أوامر BUY المفتوحة ويحافظ على أوامر SELL الوقائية."))return;try{const result=await api("/api/mainnet/emergency-stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmed:true})});setMessage("تم تفعيل قفل الطوارئ. فشل الإلغاء: "+(result.failures||[]).length);await load()}catch(error){setMessage(error.message,true)}});
$("mainnetSide").addEventListener("change",()=>{const buy=$("mainnetSide").value==="BUY";$("mainnetTakeProfitWrap").hidden=!buy;$("mainnetStopWrap").hidden=!buy;$("mainnetStopLimitWrap").hidden=!buy;$("mainnetTakeProfit").required=buy;$("mainnetStop").required=buy;$("mainnetStopLimit").required=buy;});
$("mainnetSide").dispatchEvent(new Event("change"));
$("mainnetOrderForm").addEventListener("submit",async event=>{event.preventDefault();if(!enabled)return;const side=$("mainnetSide").value,symbol=$("mainnetSymbol").value,quantity=$("mainnetQuantity").value,price=$("mainnetPrice").value;const isBuy=side==="BUY";const summary=isBuy?("إدخال BUY محمي OPOCO: "+quantity+" "+symbol+" بسعر "+price+"، TP "+$("mainnetTakeProfit").value+"، SL "+$("mainnetStop").value+". هذا أمر حقيقي على Binance. هل تؤكد؟"):("إرسال SELL LIMIT: "+quantity+" "+symbol+" بسعر "+price+" على حساب Binance الحقيقي. هل تؤكد؟");if(!window.confirm(summary))return;const id="mumin-"+(window.crypto&&crypto.randomUUID?crypto.randomUUID().replaceAll("-","").slice(0,24):String(Date.now()));$("mainnetSubmit").disabled=true;try{let path,body;if(isBuy){path="/api/mainnet/entries/protected";body={symbol,quantity,working_price:price,take_profit_price:$("mainnetTakeProfit").value,stop_price:$("mainnetStop").value,stop_limit_price:$("mainnetStopLimit").value,list_client_order_id:id,working_client_order_id:id+"-w",pending_above_client_order_id:id+"-tp",pending_below_client_order_id:id+"-sl",confirmed:true};}else{path="/api/mainnet/orders";body={side,symbol,quantity,price,client_order_id:id,confirmed:true};}const result=await api(path,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":id},body:JSON.stringify(body)});setMessage("تم إرسال الطلب. الحالة: "+(result.order?.status||result.order_list?.listOrderStatus||result.order_list?.listStatusType||"UNKNOWN"));$("mainnetQuantity").value="";$("mainnetPrice").value="";await load()}catch(error){setMessage(error.message,true)}finally{$("mainnetSubmit").disabled=!enabled}});
document.addEventListener("click",event=>{if(event.target.closest('[data-page="mainnetOperationsPage"]'))return});
})();
