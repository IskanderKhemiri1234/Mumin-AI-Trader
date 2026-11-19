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
<section class="mainnet-panel"><h3>عامل إشارات Mainnet المستقل</h3><div id="mainnetSignalDetails"></div><div class="mainnet-actions"><button class="btn" id="mainnetSignalStart">تشغيل العامل</button><button class="btn" id="mainnetSignalStop">إيقاف العامل</button></div></section>
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
<section class="mainnet-panel"><h3>المراكز الفعلية</h3><div id="mainnetPositions" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>دفتر الأوامر المحلي</h3><div id="mainnetOrders" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>أوامر Binance المفتوحة</h3><div id="mainnetExchangeOpenOrders" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>الصفقات الأخيرة</h3><div id="mainnetTrades" class="mainnet-table"></div></section>
<section class="mainnet-panel"><h3>سجل موافقات الشات والتدقيق</h3><div id="mainnetApprovals" class="mainnet-table"></div></section>
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
 const signalWorker=await api("/api/mainnet/signal-worker/status");
 $("mainnetSignalDetails").innerHTML=kv("Worker mode",signalWorker.mode||"—")+kv("Desired state",signalWorker.desired_state||"—")+kv("Recent signal records",(signalWorker.signals||[]).length)+kv("Latest worker event",signalWorker.events?.[0]?.event_type||"—");
 $("mainnetSignalStart").disabled=signalWorker.desired_state==="RUNNING";$("mainnetSignalStop").disabled=signalWorker.desired_state==="STOPPED";
 $("mainnetState").textContent=status.status||"UNKNOWN";$("mainnetState").className="mainnet-status "+(enabled&&status.can_submit?"ok":"bad");
 $("mainnetStatusDetails").innerHTML=kv("Environment",status.environment||"—")+kv("Mode",status.mode||"—")+kv("Host",status.host||"—")+kv("Unresolved orders",status.unresolved_orders??"—")+kv("Halt reason",status.halt_reason||"—");
 $("mainnetSubmit").disabled=!enabled||!status.can_submit;
 if(!enabled){$("mainnetAccountDetails").innerHTML=kv("الحالة","مقفول من إعدادات الخادم");$("mainnetPnlDetails").innerHTML=kv("التداول الحقيقي","غير مفعّل");$("mainnetBalances").innerHTML="—";$("mainnetOrders").innerHTML="—";$("mainnetExchangeOpenOrders").innerHTML="—";$("mainnetTrades").innerHTML="—";$("mainnetApprovals").innerHTML="—";setMessage("Mainnet مقفول حالياً. لا يمكن إرسال أي أمر.");return}
 const [account,pnl,orders,trades,approvals]=await Promise.all([api("/api/mainnet/account"),api("/api/mainnet/pnl"),api("/api/mainnet/orders?limit=20"),api("/api/mainnet/trades?limit=20"),api("/api/mainnet/approvals?limit=30")]);
 $("mainnetAccountDetails").innerHTML=kv("Account type",account.account_type)+kv("Spot trading permission",String(account.can_trade));
 $("mainnetPnlDetails").innerHTML=kv("Realized PnL",pnl.realized_pnl_quote??"—")+kv("Quote asset",pnl.quote_asset||"USDT")+kv("Ledger complete",String(pnl.complete))+kv("Unresolved items",(pnl.unresolved||[]).length);
 $("mainnetBalances").innerHTML=table(["Asset","Free","Locked"],(account.balances||[]).filter(x=>Number(x.free||0)+Number(x.locked||0)>0).map(x=>'<tr><td>'+escapeHtml(x.asset)+'</td><td>'+escapeHtml(x.free)+'</td><td>'+escapeHtml(x.locked)+'</td></tr>'));
 $("mainnetPositions").innerHTML=table(["Symbol","Open quantity","Cost basis"],Object.entries(pnl.symbols||{}).filter(([,v])=>Number(v.open_quantity||0)>0).map(([symbol,v])=>'<tr><td>'+escapeHtml(symbol)+'</td><td>'+escapeHtml(v.open_quantity)+'</td><td>'+escapeHtml(v.open_cost_basis_quote)+'</td></tr>'));
 const orderRows=[...(orders.orders||[]).map(x=>({...x,kind:"ORDER"})),...(orders.order_lists||[]).map(x=>({...x,kind:"OPOCO"}))];
 $("mainnetOrders").innerHTML=table(["Type","Symbol","Side","Qty","Price","State","Action"],orderRows.map(x=>{const active=["NEW","PARTIALLY_FILLED","SUBMITTING","UNKNOWN","CANCEL_PENDING","WORKING","PROTECTION_ARMED","EXECUTING","EXEC_STARTED"].includes(x.state);const isList=x.kind==="OPOCO";const id=isList?x.list_client_order_id:x.client_order_id;return '<tr><td>'+x.kind+'</td><td>'+escapeHtml(x.symbol)+'</td><td>'+escapeHtml(x.side)+'</td><td>'+escapeHtml(x.quantity)+'</td><td>'+escapeHtml(x.price||"—")+'</td><td>'+escapeHtml(x.state)+'</td><td>'+(active?(isList?'<button class="btn" data-mainnet-cancel-list="'+escapeHtml(id)+'" data-symbol="'+escapeHtml(x.symbol)+'">إلغاء الحماية</button>':'<button class="btn" data-mainnet-cancel="'+escapeHtml(id)+'" data-symbol="'+escapeHtml(x.symbol)+'">إلغاء</button>'):'—')+'</td></tr>'}));
 $("mainnetExchangeOpenOrders").innerHTML=table(["Symbol","Side","Original Qty","Executed","Price","Status"],(orders.exchange_open_orders||[]).map(x=>'<tr><td>'+escapeHtml(x.symbol)+'</td><td>'+escapeHtml(x.side)+'</td><td>'+escapeHtml(x.origQty)+'</td><td>'+escapeHtml(x.executedQty)+'</td><td>'+escapeHtml(x.price)+'</td><td>'+escapeHtml(x.status)+'</td></tr>'));
 $("mainnetTrades").innerHTML=table(["Symbol","Side","Qty","Price","Fee"],(trades.trades||[]).slice(0,20).map(x=>'<tr><td>'+escapeHtml(x.symbol)+'</td><td>'+((x.is_buyer)?"BUY":"SELL")+'</td><td>'+escapeHtml(x.quantity)+'</td><td>'+escapeHtml(x.price)+'</td><td>'+escapeHtml(x.commission)+" "+escapeHtml(x.commission_asset)+'</td></tr>'));
 $("mainnetApprovals").innerHTML=table(["Approval ID","Type","State","Created","Error"],(approvals.approvals||[]).map(x=>'<tr><td>'+escapeHtml(String(x.approval_id||"").slice(0,12))+'</td><td>'+escapeHtml(x.proposal_type)+'</td><td>'+escapeHtml(x.state)+'</td><td>'+escapeHtml(x.created_at)+'</td><td>'+escapeHtml(x.last_error||"—")+'</td></tr>'));
 setMessage("تم تحديث بيانات حساب Binance Mainnet من الخادم.");
 }catch(error){setMessage(error.message,true);$("mainnetState").textContent="ERROR";$("mainnetState").className="mainnet-status bad"}finally{busy=false;$("mainnetRefresh").disabled=false}}
async function setSignalWorkerState(desiredState){
 try{
  const current=await api("/api/mainnet/signal-worker/status");
  if(desiredState==="RUNNING"&&current.mode==="AUTONOMOUS_PROTECTED_OPOCO"&&!window.confirm("تحذير: هذا سيبدأ عاملاً آلياً يرسل أوامر OPOCO إلى حساب Binance الحقيقي ويستخدم أموالاً فعلية. هل تؤكد تشغيل التداول الآلي؟"))return;
  if(desiredState==="RUNNING"&&current.mode==="SHADOW_ONLY"&&!window.confirm("تشغيل عامل الإشارات في وضع Shadow فقط؛ سيقرأ بيانات السوق ويسجل الإشارات دون إرسال أوامر. متابعة؟"))return;
  if(desiredState==="STOPPED"&&!window.confirm("إيقاف عامل إشارات Mainnet؟ لن تُلغى أوامر الحماية المفتوحة تلقائياً."))return;
  await api("/api/mainnet/signal-worker/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({desired_state:desiredState})});
  setMessage("تم حفظ الحالة المطلوبة للعامل: "+desiredState);
  await load();
 }catch(error){setMessage(error.message,true)}
}
$("mainnetSignalStart").addEventListener("click",()=>setSignalWorkerState("RUNNING"));
$("mainnetSignalStop").addEventListener("click",()=>setSignalWorkerState("STOPPED"));
$("mainnetRefresh").addEventListener("click",load);
$("mainnetReconcile").addEventListener("click",async()=>{if(!window.confirm("تنفيذ مصالحة كاملة للأرصدة والطلبات والصفقات؟"))return;try{const result=await api("/api/mainnet/reconcile",{method:"POST"});setMessage("اكتملت المصالحة. الاختلافات: "+(result.discrepancies||[]).length);await load()}catch(error){setMessage(error.message,true)}});
$("mainnetStop").addEventListener("click",async()=>{if(!window.confirm("تأكيد إيقاف Mainnet؟ سيوقف الإدخالات ويلغي أوامر BUY المفتوحة ويحافظ على أوامر SELL الوقائية."))return;try{const result=await api("/api/mainnet/emergency-stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmed:true})});setMessage("تم تفعيل قفل الطوارئ. فشل الإلغاء: "+(result.failures||[]).length);await load()}catch(error){setMessage(error.message,true)}});
$("mainnetSide").addEventListener("change",()=>{const buy=$("mainnetSide").value==="BUY";$("mainnetTakeProfitWrap").hidden=!buy;$("mainnetStopWrap").hidden=!buy;$("mainnetStopLimitWrap").hidden=!buy;$("mainnetTakeProfit").required=buy;$("mainnetStop").required=buy;$("mainnetStopLimit").required=buy;});
$("mainnetSide").dispatchEvent(new Event("change"));
$("mainnetOrderForm").addEventListener("submit",async event=>{
 event.preventDefault();
 if(!enabled)return;
 const side=$("mainnetSide").value,symbol=$("mainnetSymbol").value,quantity=$("mainnetQuantity").value,price=$("mainnetPrice").value;
 const isBuy=side==="BUY";
 const command=isBuy
  ? `BUY ${symbol} qty=${quantity} price=${price} tp=${$("mainnetTakeProfit").value} stop=${$("mainnetStop").value} stop_limit=${$("mainnetStopLimit").value}`
  : `SELL ${symbol} qty=${quantity} price=${price}`;
 $("mainnetSubmit").disabled=true;
 try{
  const prepared=await api("/api/operations-chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:command})});
  const proposal=prepared.proposal;
  if(!proposal)throw new Error(prepared.answer||"لم يتم إنشاء موافقة صالحة للأمر.");
  const buy=proposal.type==="protected_buy";
  const summary=buy
   ?("إدخال BUY محمي OPOCO: "+proposal.quantity+" "+proposal.symbol+" بسعر "+proposal.working_price+"، TP "+proposal.take_profit_price+"، SL "+proposal.stop_price+". هذا أمر حقيقي على Binance. هل تؤكد؟")
   :("إرسال SELL LIMIT: "+proposal.quantity+" "+proposal.symbol+" بسعر "+proposal.price+" على حساب Binance الحقيقي. هل تؤكد؟");
  if(!window.confirm(summary))return;
  const endpoint=buy?"/api/mainnet/entries/protected":"/api/mainnet/orders";
  const id=buy?proposal.list_client_order_id:proposal.client_order_id;
  const body={...proposal,confirmed:true};
  const result=await api(endpoint,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":id},body:JSON.stringify(body)});
  setMessage("تم إرسال الطلب. الحالة: "+(result.order?.status||result.order_list?.listOrderStatus||result.order_list?.listStatusType||"UNKNOWN"));
  $("mainnetQuantity").value="";
  $("mainnetPrice").value="";
  await load();
 }catch(error){setMessage(error.message,true)}
 finally{$("mainnetSubmit").disabled=!enabled}
});
document.addEventListener("click",async event=>{const button=event.target.closest("[data-mainnet-cancel-list]");if(!button)return;const id=button.dataset.mainnetCancelList,symbol=button.dataset.symbol;if(!window.confirm("تحذير: إلغاء قائمة الحماية قد يترك مركزاً مفتوحاً دون وقف خسارة. هل تؤكد؟"))return;try{await api("/api/mainnet/order-lists/"+encodeURIComponent(id),{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol,confirmed:true})});setMessage("تم إرسال طلب إلغاء قائمة الحماية؛ راجع المركز يدوياً فوراً.");await load()}catch(error){setMessage(error.message,true)}});
document.addEventListener("click",async event=>{const button=event.target.closest("[data-mainnet-cancel]");if(!button)return;const id=button.dataset.mainnetCancel,symbol=button.dataset.symbol;if(!window.confirm("تأكيد إلغاء الأمر "+id+"؟"))return;try{await api("/api/mainnet/orders/"+encodeURIComponent(id),{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol,confirmed:true})});setMessage("تم إرسال طلب الإلغاء؛ تحقّق من حالة الأمر بعد المصالحة.");await load()}catch(error){setMessage(error.message,true)}});

})();
