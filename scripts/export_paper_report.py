from __future__ import annotations

import argparse
import html
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any


def load_snapshot(database: str) -> dict[str, Any]:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"Paper ledger not found: {path}")

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        account = connection.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
        if account is None:
            raise RuntimeError("Paper ledger has no account row.")

        prices = {
            row["symbol"]: Decimal(row["price"])
            for row in connection.execute(
                """SELECT m.symbol,m.price FROM bot_market_ticks m
                   JOIN (SELECT symbol,MAX(id) AS max_id FROM bot_market_ticks GROUP BY symbol) x
                   ON x.max_id=m.id"""
            )
        }
        positions = []
        for row in connection.execute(
            "SELECT symbol,quantity,entry_price,opened_at FROM bot_positions ORDER BY symbol"
        ):
            quantity = Decimal(row["quantity"])
            entry = Decimal(row["entry_price"])
            mark = prices.get(row["symbol"])
            positions.append({
                "symbol": row["symbol"],
                "quantity": quantity,
                "entry_price": entry,
                "mark_price": mark,
                "market_value": quantity * mark if mark is not None else None,
                "unrealized_pnl": quantity * (mark - entry) if mark is not None else None,
                "opened_at": row["opened_at"],
            })

        cash = Decimal(account["cash"])
        equity = cash + sum(
            (item["market_value"] or Decimal("0") for item in positions), Decimal("0")
        )
        initial = Decimal(account["initial_cash"])
        high_water = Decimal(account["high_watermark"])
        daily_pnl = equity - Decimal(account["daily_start_equity"])
        counts = connection.execute(
            """SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN CAST(net_pnl AS REAL)>0 THEN 1 ELSE 0 END),0) AS wins,
               COALESCE(SUM(CASE WHEN CAST(net_pnl AS REAL)<0 THEN 1 ELSE 0 END),0) AS losses
               FROM bot_fills WHERE side='SELL'"""
        ).fetchone()
        fills = [dict(row) for row in connection.execute(
            """SELECT tick,symbol,side,quantity,fill_price,net_pnl,data_source,created_at
               FROM bot_fills ORDER BY fill_id DESC LIMIT 12"""
        )]
        history = [dict(row) for row in connection.execute(
            """SELECT tick,equity,created_at FROM bot_equity ORDER BY tick DESC LIMIT 48"""
        )]
        source = connection.execute(
            "SELECT data_source FROM bot_market_ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest_tick = connection.execute(
            "SELECT signal,action,price FROM bot_equity ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        signals = {}
        if latest_tick and latest_tick["signal"]:
            for item in str(latest_tick["signal"]).split(","):
                if ":" in item:
                    symbol, signal = item.split(":", 1)
                    signals[symbol] = signal
        return {
            "tick": int(account["tick_count"]),
            "initial_cash": initial,
            "cash": cash,
            "equity": equity,
            "realized_pnl": Decimal(account["realized_pnl"]),
            "daily_pnl": daily_pnl,
            "fees": Decimal(account["fees_paid"]),
            "high_watermark": high_water,
            "drawdown": max(Decimal("0"), high_water - equity),
            "drawdown_pct": (
                max(Decimal("0"), high_water - equity) / high_water * Decimal("100")
                if high_water > 0 else Decimal("0")
            ),
            "return_pct": (
                (equity - initial) / initial * Decimal("100") if initial > 0 else Decimal("0")
            ),
            "trades": int(counts["total"]),
            "wins": int(counts["wins"]),
            "losses": int(counts["losses"]),
            "positions": positions,
            "market_prices": prices,
            "signals": signals,
            "last_action": str(latest_tick["action"]) if latest_tick else "NO_TICK",
            "last_price": Decimal(latest_tick["price"]) if latest_tick else None,
            "fills": fills,
            "history": list(reversed(history)),
            "halted": bool(account["halted"]),
            "halt_reason": str(account["halt_reason"]),
            "updated_at": str(account["updated_at"]),
            "source": source["data_source"] if source else "NO_MARKET_DATA",
        }
    finally:
        connection.close()


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:,.4f} USDT"


def render_markdown(snapshot: dict[str, Any]) -> str:
    status = "⛔ إيقاف حماية مفعّل" if snapshot["halted"] else "🟢 يعمل ضمن حدود Paper"
    rows = [
        "# Mumin AI Trader — تقرير Paper",
        "",
        "> **تنبيه:** محاكاة فقط، لا توجد أوامر Binance حقيقية ولا أرباح قابلة للسحب.",
        "",
        f"**الحالة:** {status}  ",
        f"**آخر تحديث:** {snapshot['updated_at']} UTC  ",
        f"**مصدر البيانات:** {snapshot['source']}  ",
        f"**الدورة:** {snapshot['tick']}",
        "",
        "| المؤشر | القيمة |",
        "|---|---:|",
        f"| رأس المال الافتراضي | {_money(snapshot['initial_cash'])} |",
        f"| قيمة المحفظة | {_money(snapshot['equity'])} |",
        f"| النقد | {_money(snapshot['cash'])} |",
        f"| صافي الربح/الخسارة المحقق | {_money(snapshot['realized_pnl'])} |",
        f"| أداء اليوم | {_money(snapshot['daily_pnl'])} |",
        f"| العائد الكلي | {snapshot['return_pct']:.3f}% |",
        f"| الرسوم المقدّرة | {_money(snapshot['fees'])} |",
        f"| الصفقات المغلقة | {snapshot['trades']} (رابحة {snapshot['wins']} / خاسرة {snapshot['losses']}) |",
        f"| المراكز المفتوحة | {len(snapshot['positions'])} |",
        f"| التراجع من القمة | {_money(snapshot['drawdown'])} ({snapshot['drawdown_pct']:.3f}%) |",
        "",
        "## حدود المخاطر المطبقة في محرك Paper",
        "",
        "- وقف الخسارة المحاكى: **0.50% من تكلفة الدخول** وفق صافي الخروج بعد رسوم الدخول والخروج والانزلاق السعري.",
        "- جني الربح المحاكى: **1.00% من تكلفة الدخول** وفق صافي الخروج بعد رسوم الدخول والخروج والانزلاق السعري.",
        "- حد الخسارة اليومية: **2.00%** من قيمة بداية اليوم.",
        "- حد التراجع: **5.00%** من أعلى قيمة مسجلة.",
        "- أقصى تخصيص للصفقة: **5 USDT**؛ احتياطي نقدي **20%**؛ حد أقصى مركزان.",
        "",
        "تُقاس عتبتا 0.50% و1.00% على صافي نتيجة الخروج نسبةً إلى تكلفة الدخول، بعد رسوم الطرفين والانزلاق المحاكى. هذه ضوابط محاكاة وليست أوامر حماية لدى Binance؛ وقد تتجاوز فجوات الأسعار أو تأخر الجدولة العتبات المحددة.",
        "",
        "## مراقبة السوق وإشارات Smart Sniper",
        "",
        f"**آخر إجراء:** {snapshot['last_action']}",
        "",
        "| الرمز | آخر سعر عام | الإشارة |",
        "|---|---:|---|",
    ]
    if snapshot["market_prices"]:
        for symbol, price in sorted(snapshot["market_prices"].items()):
            rows.append(
                f"| {symbol} | {_money(price)} | {snapshot['signals'].get(symbol, '—')} |"
            )
    else:
        rows.append("| لا توجد بيانات سوق حديثة | — | — |")
    rows.extend([
        "",
        "## المراكز المفتوحة",
        "",
        "| الأصل | الكمية | الدخول | السعر المرجعي | القيمة |",
        "|---|---:|---:|---:|---:|",
    ])
    if snapshot["positions"]:
        for item in snapshot["positions"]:
            rows.append(
                f"| {item['symbol']} | {item['quantity']:.8f} | {_money(item['entry_price'])} "
                f"| {_money(item['mark_price'])} | {_money(item['market_value'])} |"
            )
    else:
        rows.append("| لا توجد مراكز | — | — | — | — |")
    rows.extend(["", "## آخر الصفقات", "", "| الدورة | الأصل | الجانب | سعر التنفيذ المحاكى | صافي النتيجة | الوقت |", "|---:|---|---|---:|---:|---|"])
    if snapshot["fills"]:
        for item in snapshot["fills"]:
            rows.append(
                f"| {item['tick']} | {item['symbol']} | {item['side']} "
                f"| {item['fill_price']} | {item['net_pnl']} USDT | {item['created_at']} |"
            )
    else:
        rows.append("| — | لا توجد صفقات مغلقة بعد | — | — | — | — |")
    rows.extend(["", "## ملاحظة التشغيل", "", "يُطلب تشغيل دورة كل خمس دقائق من GitHub Actions، لكن GitHub قد يؤخر أو يتجاوز مواعيد الجدولة. هذا ليس خادماً متصلاً 24/7، ولا ينبغي الاعتماد عليه لحماية مراكز مالية حقيقية.", ""])
    return "\n".join(rows)


def render_html(snapshot: dict[str, Any]) -> str:
    labels = ["قيمة المحفظة", "النقد المتاح", "الربح المحقق", "أداء اليوم", "الرسوم"]
    values = [
        _money(snapshot["equity"]), _money(snapshot["cash"]),
        _money(snapshot["realized_pnl"]), _money(snapshot["daily_pnl"]),
        _money(snapshot["fees"]),
    ]
    metric_cards = "".join(
        f'<article class="metric"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>'
        for label, value in zip(labels, values)
    )

    position_rows = "".join(
        "<tr>"
        f"<td><b>{html.escape(item['symbol'])}</b></td>"
        f"<td>{item['quantity']:.8f}</td>"
        f"<td>{html.escape(_money(item['entry_price']))}</td>"
        f"<td>{html.escape(_money(item['mark_price']))}</td>"
        f"<td>{html.escape(_money(item['market_value']))}</td>"
        "</tr>"
        for item in snapshot["positions"]
    ) or '<tr><td colspan="5" class="empty">لا توجد مراكز مفتوحة حالياً</td></tr>'

    signal_styles = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}
    watch_cards = "".join(
        (
            '<article class="asset-card">'
            f'<div class="asset-top"><span class="asset-symbol">{html.escape(symbol)}</span>'
            f'<span class="signal {signal_styles.get(snapshot["signals"].get(symbol, ""), "muted-signal")}">'
            f'{html.escape(snapshot["signals"].get(symbol, "—"))}</span></div>'
            f'<strong class="asset-price">{html.escape(_money(price))}</strong>'
            '<span class="asset-caption">آخر سعر عام مسجل</span>'
            '</article>'
        )
        for symbol, price in sorted(snapshot["market_prices"].items())
    ) or '<p class="empty">لا توجد بيانات سوق حديثة في هذه الدورة</p>'

    fill_rows = "".join(
        "<tr>"
        f"<td>{item['tick']}</td><td><b>{html.escape(item['symbol'])}</b></td>"
        f"<td>{html.escape(item['side'])}</td><td>{html.escape(item['fill_price'])}</td>"
        f"<td>{html.escape(item['net_pnl'])} USDT</td>"
        f"<td>{html.escape(item['created_at'])}</td></tr>"
        for item in snapshot["fills"]
    ) or '<tr><td colspan="6" class="empty">لا توجد صفقات مسجلة بعد — ننتظر إشارة مطابقة</td></tr>'

    history = snapshot["history"]
    points = [float(Decimal(str(item["equity"]))) for item in history]
    if len(points) > 1:
        low, high = min(points), max(points)
        span = max(high - low, 0.000001)
        coords = [
            f"{24 + i * 552 / (len(points) - 1):.1f},{166 - (value - low) / span * 132:.1f}"
            for i, value in enumerate(points)
        ]
        chart = (
            '<defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1">'
            '<stop offset="0%" stop-color="#d9bd78" stop-opacity=".30"/>'
            '<stop offset="100%" stop-color="#d9bd78" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="24,178 {" ".join(coords)} 576,178" fill="url(#area)"/>'
            '<polyline fill="none" stroke="#e7cb86" stroke-width="3.5" '
            'stroke-linecap="round" stroke-linejoin="round" points="' + " ".join(coords) + '"/>'
        )
    else:
        chart = '<text x="300" y="96" text-anchor="middle" fill="#9eb5a8">سيظهر منحنى الأداء بعد تسجيل دورات إضافية</text>'

    state = "متوقف — حماية المخاطر" if snapshot["halted"] else "نشط — محاكاة Paper"
    state_class = "danger" if snapshot["halted"] else "active"
    halt = f'<p class="halt">{html.escape(snapshot["halt_reason"])}</p>' if snapshot["halted"] else ""
    action = html.escape(snapshot["last_action"])
    updated = html.escape(snapshot["updated_at"])
    source = html.escape(snapshot["source"])
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#071713">
<meta name="description" content="لوحة متابعة Mumin AI Trader — تقرير Paper">
<title>Mumin AI Trader | لوحة Paper</title>
<style>
:root{{--bg:#071713;--surface:#0e251d;--surface2:#132f24;--line:#294a39;--gold:#e5c982;--text:#f5f3e9;--muted:#9eb5a8;--green:#65d6a0;--red:#ff928c}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth;scroll-padding-top:78px}}
body{{margin:0;background:radial-gradient(ellipse at 85% -15%,#1c4934 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif}}
a{{color:inherit;text-decoration:none}}
main{{width:min(100%,1040px);margin:auto;padding:20px 18px calc(42px + env(safe-area-inset-bottom))}}
.hero{{position:relative;overflow:hidden;padding:24px;border:1px solid #3a5b43;border-radius:24px;background:linear-gradient(135deg,rgba(25,66,47,.98),rgba(9,31,23,.98));box-shadow:0 18px 48px #0005}}
.hero:after{{content:"";position:absolute;width:210px;height:210px;border:1px solid #d9bd7830;border-radius:50%;left:-75px;top:-105px;box-shadow:0 0 0 24px #d9bd7810,0 0 0 48px #d9bd7808;pointer-events:none}}
.brand{{color:var(--gold);font-size:11px;font-weight:800;letter-spacing:2px}}
h1{{margin:7px 0 3px;font-size:clamp(23px,5vw,34px);line-height:1.25}}
.subtitle,.muted{{color:var(--muted);font-size:12px}}
.hero-meta{{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:10px}}
.pill{{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border:1px solid var(--line);border-radius:99px;background:#071a14aa;font-size:12px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green)}}
.active{{color:var(--green)}}.danger{{color:var(--red)}}
.paper-warning{{position:relative;margin-top:18px;padding:13px 15px;border:1px solid #9a804b;border-radius:14px;background:#332b1d;color:#f1dfb3;font-size:13px}}
.paper-warning b{{color:#ffe5a2}}
nav{{position:sticky;top:8px;z-index:5;display:flex;gap:7px;overflow-x:auto;margin:14px 0;padding:8px;border:1px solid #294a3980;border-radius:15px;background:#091c16eF;backdrop-filter:blur(12px);scrollbar-width:none}}
nav::-webkit-scrollbar{{display:none}}
nav a{{flex:0 0 auto;padding:7px 12px;border-radius:10px;color:var(--muted);font-size:12px}}
nav a:focus,nav a:hover{{background:#1b3b2b;color:var(--gold)}}
.metrics{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0}}
.metric,.panel{{border:1px solid var(--line);border-radius:19px;background:linear-gradient(150deg,#122b21,#0b2018);box-shadow:0 10px 30px #0002}}
.metric{{min-width:0;padding:15px}}
.metric span{{display:block;color:var(--muted);font-size:11px}}
.metric strong{{display:block;margin-top:7px;color:var(--gold);font-size:clamp(14px,2vw,19px);line-height:1.4;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}}
.panel{{padding:18px;margin-top:14px;scroll-margin-top:80px}}
.panel-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:13px}}
h2{{margin:0;color:var(--gold);font-size:16px}}
.kicker{{color:var(--muted);font-size:11px}}
.asset-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
.asset-card{{padding:15px;border:1px solid #345541;border-radius:16px;background:linear-gradient(145deg,#17382a,#0b2119)}}
.asset-top{{display:flex;justify-content:space-between;align-items:center;gap:6px}}
.asset-symbol{{font-weight:800;font-size:13px;letter-spacing:.3px}}
.signal{{display:inline-flex;padding:3px 9px;border-radius:99px;font-size:10px;font-weight:800;letter-spacing:.5px}}
.buy{{background:#164d36;color:#7ce2ae}}.sell{{background:#552c2a;color:#ffaaa3}}.hold{{background:#3b3828;color:#e8d69c}}
.muted-signal{{background:#263c32;color:#b7c9bd}}
.asset-price{{display:block;margin-top:13px;color:var(--gold);font-size:clamp(14px,2.5vw,19px);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}
.asset-caption{{color:var(--muted);font-size:10px}}
.chart-wrap{{padding:8px;border:1px solid #213f30;border-radius:15px;background:#081b14}}
svg{{display:block;width:100%;height:190px}}
.stats-line{{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:10px;color:var(--muted);font-size:12px}}
.risk-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}}
.risk-item{{padding:13px;border:1px solid #294737;border-radius:14px;background:#091d16}}
.risk-item span{{display:block;color:var(--muted);font-size:11px}}
.risk-item b{{display:block;margin-top:4px;color:var(--gold);font-size:20px}}
.table-wrap{{overflow-x:auto;border:1px solid #294737;border-radius:13px;overscroll-behavior-inline:contain}}
table{{width:100%;border-collapse:collapse;min-width:560px;font-size:12px}}
th,td{{padding:11px 12px;border-bottom:1px solid #294737;text-align:right;white-space:nowrap}}
th{{background:#10291e;color:var(--muted);font-weight:600}}
tbody tr:last-child td{{border-bottom:0}}
tbody tr:hover{{background:#ffffff08}}
.empty{{padding:22px;text-align:center;color:var(--muted);font-size:12px}}
.halt{{color:var(--red)}}
footer{{padding:20px 5px;color:#839c8e;font-size:11px;text-align:center}}
@media(max-width:760px){{main{{padding:12px 12px calc(32px + env(safe-area-inset-bottom))}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.metrics .metric:first-child{{grid-column:span 2}}.metric{{padding:13px}}.asset-grid{{gap:8px}}.asset-card{{padding:12px}}}}
@media(max-width:420px){{.hero{{padding:19px}}.panel{{padding:14px}}.asset-grid{{grid-template-columns:1fr}}.asset-card{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.asset-price{{margin:0;font-size:14px}}.asset-caption{{display:none}}.risk-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.hero-meta{{font-size:11px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}*{{transition:none!important}}}}
</style>
</head>
<body>
<main>
<header class="hero" id="top">
<div class="brand">MUMIN · SMART SNIPER</div>
<h1>مركز متابعة التداول الذكي</h1>
<p class="subtitle">لوحة الأداء والمحفظة وإشارات السوق — نسخة الهاتف</p>
<div class="hero-meta">
<span class="pill"><i class="dot"></i><span class="{state_class}">{html.escape(state)}</span></span>
<span class="pill">الدورة #{snapshot['tick']}</span>
<span class="pill">المصدر: {source}</span>
</div>
<p class="subtitle">آخر تحديث: {updated} UTC</p>
{halt}
<div class="paper-warning"><b>Paper فقط — وضع تجريبي.</b> جميع عمليات الشراء والبيع محاكاة داخل SQLite؛ لا تُرسل أوامر إلى Binance ولا تمثل أرباحاً قابلة للسحب. مستويات وقف الخسارة وجني الربح محاكاة وليست أوامر حماية في المنصة.</div>
</header>

<nav aria-label="التنقل داخل التقرير">
<a href="#overview">نظرة عامة</a><a href="#market">السوق والإشارات</a><a href="#performance">الأداء</a><a href="#risk">المخاطر</a><a href="#positions">المراكز</a><a href="#trades">الصفقات</a>
</nav>

<section class="metrics" id="overview">{metric_cards}</section>

<section class="panel" id="market">
<div class="panel-head"><h2>مراقبة السوق · Smart Sniper</h2><span class="kicker">Public market data</span></div>
<div class="asset-grid">{watch_cards}</div>
<p class="muted">آخر إجراء للمحرك: <b>{action}</b>. لا يدخل المحرك الصفقة إلا عند تحقق شروط الإشارة وحدود رأس المال والمخاطر.</p>
</section>

<section class="panel" id="performance">
<div class="panel-head"><h2>منحنى قيمة المحفظة</h2><span class="kicker">آخر {len(history)} دورة</span></div>
<div class="chart-wrap"><svg viewBox="0 0 600 190" role="img" aria-label="منحنى قيمة محفظة Paper">{chart}</svg></div>
<div class="stats-line"><span>العائد الكلي: <b>{snapshot['return_pct']:.3f}%</b></span><span>الصفقات المغلقة: <b>{snapshot['trades']}</b></span><span>رابحة: <b>{snapshot['wins']}</b></span><span>خاسرة: <b>{snapshot['losses']}</b></span></div>
</section>

<section class="panel" id="risk">
<div class="panel-head"><h2>حواجز المخاطر</h2><span class="kicker">Paper rules</span></div>
<div class="risk-grid">
<div class="risk-item"><span>وقف صافٍ بعد الرسوم والانزلاق</span><b>0.50%</b></div>
<div class="risk-item"><span>هدف صافٍ بعد الرسوم والانزلاق</span><b>1.00%</b></div>
<div class="risk-item"><span>حد الخسارة اليومية</span><b>2.00%</b></div>
<div class="risk-item"><span>حد التراجع</span><b>5.00%</b></div>
<div class="risk-item"><span>أقصى تخصيص للصفقة</span><b>5 USDT</b></div>
<div class="risk-item"><span>الاحتياطي النقدي</span><b>20%</b></div>
</div>
<p class="muted">يُحسب الوقف والهدف على صافي نتيجة الخروج بعد رسوم الدخول والخروج والانزلاق المحاكى، كنسبة من تكلفة الدخول. هذه ضوابط للمحاكاة فقط؛ وقد تتجاوز فجوات الأسعار أو تأخر الجدولة العتبات المحددة.</p>
</section>

<section class="panel" id="positions">
<div class="panel-head"><h2>المراكز المفتوحة</h2><span class="kicker">{len(snapshot['positions'])} مركز</span></div>
<div class="table-wrap"><table><thead><tr><th>الأصل</th><th>الكمية</th><th>سعر الدخول</th><th>السعر المرجعي</th><th>القيمة</th></tr></thead><tbody>{position_rows}</tbody></table></div>
</section>

<section class="panel" id="trades">
<div class="panel-head"><h2>سجل الصفقات</h2><span class="kicker">آخر {len(snapshot['fills'])} حركة</span></div>
<div class="table-wrap"><table><thead><tr><th>الدورة</th><th>الأصل</th><th>الجانب</th><th>سعر التنفيذ</th><th>صافي النتيجة</th><th>الوقت</th></tr></thead><tbody>{fill_rows}</tbody></table></div>
</section>

<footer>التشغيل المجدول كل خمس دقائق هو أفضل جهد من GitHub وليس خادماً دائماً 24/7. هذه لوحة متابعة تعليمية وليست نصيحة استثمارية أو ضماناً للربح.</footer>
</main>
</body>
</html>"""



def main() -> None:
    parser = argparse.ArgumentParser(description="Export a read-only mobile Paper report.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--markdown", required=True)
    parser.add_argument("--html", required=True)
    args = parser.parse_args()
    snapshot = load_snapshot(args.db)
    markdown_path, html_path = Path(args.markdown), Path(args.html)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(snapshot), encoding="utf-8")
    html_path.write_text(render_html(snapshot), encoding="utf-8")
    print(render_markdown(snapshot))


if __name__ == "__main__":
    main()
