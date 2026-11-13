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
        f"> **تنبيه:** محاكاة فقط، لا توجد أوامر Binance حقيقية ولا أرباح قابلة للسحب.",
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
        "- وقف الخسارة المحاكى: **0.50%** من سعر الدخول.",
        "- جني الربح المحاكى: **1.00%** من سعر الدخول.",
        "- حد الخسارة اليومية: **2.00%** من قيمة بداية اليوم.",
        "- حد التراجع: **5.00%** من أعلى قيمة مسجلة.",
        "- أقصى تخصيص للصفقة: **5 USDT**؛ احتياطي نقدي **20%**؛ حد أقصى مركزان.",
        "",
        "هذه حدود منطقية داخل المحاكاة وليست أوامر حماية لدى Binance، ولا تمنع فجوات الأسعار أو تأخر التشغيل.",
        "",
        "## المراكز المفتوحة",
        "",
        "| الأصل | الكمية | الدخول | السعر المرجعي | القيمة |",
        "|---|---:|---:|---:|---:|",
    ]
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
    labels = ["قيمة المحفظة", "النقد", "الربح المحقق", "أداء اليوم", "الرسوم"]
    values = [
        _money(snapshot["equity"]), _money(snapshot["cash"]),
        _money(snapshot["realized_pnl"]), _money(snapshot["daily_pnl"]),
        _money(snapshot["fees"]),
    ]
    cards = "".join(
        f'<article class="metric"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></article>'
        for label, value in zip(labels, values)
    )
    position_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['symbol'])}</td>"
        f"<td>{item['quantity']:.8f}</td>"
        f"<td>{html.escape(_money(item['entry_price']))}</td>"
        f"<td>{html.escape(_money(item['mark_price']))}</td>"
        f"<td>{html.escape(_money(item['market_value']))}</td>"
        "</tr>"
        for item in snapshot["positions"]
    ) or '<tr><td colspan="5" class="empty">لا توجد مراكز مفتوحة</td></tr>'
    fill_rows = "".join(
        "<tr>"
        f"<td>{item['tick']}</td><td>{html.escape(item['symbol'])}</td>"
        f"<td>{html.escape(item['side'])}</td><td>{html.escape(item['fill_price'])}</td>"
        f"<td>{html.escape(item['net_pnl'])} USDT</td>"
        f"<td>{html.escape(item['created_at'])}</td></tr>"
        for item in snapshot["fills"]
    ) or '<tr><td colspan="6" class="empty">لا توجد صفقات مغلقة بعد</td></tr>'
    history = snapshot["history"]
    points = [float(Decimal(str(item["equity"]))) for item in history]
    if len(points) > 1:
        low, high = min(points), max(points)
        span = max(high - low, 0.000001)
        coords = [
            f"{20 + i * 560 / (len(points) - 1):.1f},{170 - (value - low) / span * 140:.1f}"
            for i, value in enumerate(points)
        ]
        chart = '<polyline fill="none" stroke="#e5c477" stroke-width="3" points="' + " ".join(coords) + '"/>'
    else:
        chart = '<text x="300" y="95" text-anchor="middle" fill="#a7b9ad">بانتظار المزيد من الدورات</text>'
    state = "موقوف بسبب حد المخاطر" if snapshot["halted"] else "نشط في Paper"
    halt = f'<p class="halt">{html.escape(snapshot["halt_reason"])}</p>' if snapshot["halted"] else ""
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#071a16"><title>Mumin AI Trader — Paper</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#071a16;color:#f4f3eb;font:15px/1.65 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:auto;padding:24px 16px 50px}}header,.panel,.metric{{background:linear-gradient(145deg,#123629,#0b211a);border:1px solid #285240;border-radius:18px}}
header{{padding:22px;margin-bottom:16px}}h1{{margin:0;color:#f0d89d;font-size:25px}}.sub,.muted{{color:#a8bbae;font-size:12px}}
.alert{{margin-top:15px;padding:12px;border:1px solid #987a45;border-radius:12px;color:#f0d89d;background:#332b1b}}
.status{{display:inline-block;padding:5px 10px;border-radius:30px;background:#214a36;color:#79e0ae;font-size:12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}}
.metric{{padding:15px}}.metric span{{display:block;color:#a8bbae;font-size:12px}}.metric strong{{display:block;margin-top:8px;font-size:20px;color:#f0d89d;overflow-wrap:anywhere}}
.panel{{padding:16px;margin-top:14px;overflow:hidden}}h2{{font-size:16px;color:#f0d89d;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:12px;min-width:570px}}th,td{{padding:10px;border-bottom:1px solid #285240;text-align:right}}th{{color:#a8bbae}}.scroll{{overflow:auto}}.empty{{text-align:center;color:#a8bbae;padding:20px}}
svg{{width:100%;height:190px;background:#091d17;border-radius:12px}}.risk{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.risk div{{padding:12px;background:#091d17;border-radius:12px;color:#dfe8df}}.risk b{{display:block;color:#f0d89d;font-size:19px}}
.halt{{color:#ff9a92}}footer{{margin-top:20px;color:#839d8f;font-size:11px}}
@media(max-width:480px){{main{{padding:12px}}h1{{font-size:21px}}.metric strong{{font-size:17px}}}}
</style></head><body><main>
<header><div class="muted">MUMIN AI TRADER · MOBILE PAPER REPORT</div><h1>مركز متابعة التداول الذكي</h1>
<p class="sub">آخر تحديث: {html.escape(snapshot['updated_at'])} UTC · الدورة {snapshot['tick']} · المصدر: {html.escape(snapshot['source'])}</p>
<span class="status">{html.escape(state)}</span>{halt}
<div class="alert"><b>Paper فقط:</b> هذا التقرير يعرض محاكاة افتراضية بقيمة 70 USDT. لا توجد أوامر حقيقية أو أرباح قابلة للسحب. وقف الخسارة وجني الربح محاكيان وليسا أوامر حماية لدى Binance.</div></header>
<section class="metrics">{cards}</section>
<section class="panel"><h2>منحنى قيمة المحفظة</h2><svg viewBox="0 0 600 190" role="img" aria-label="Paper equity chart">{chart}</svg>
<p class="muted">العائد الكلي: {snapshot['return_pct']:.3f}% · الصفقات المغلقة: {snapshot['trades']} · رابحة: {snapshot['wins']} · خاسرة: {snapshot['losses']}</p></section>
<section class="panel"><h2>حدود المخاطر</h2><div class="risk">
<div>وقف الخسارة المحاكى<b>0.50%</b></div><div>جني الربح المحاكى<b>1.00%</b></div>
<div>الخسارة اليومية القصوى<b>2.00%</b></div><div>التراجع الأقصى<b>5.00%</b></div>
<div>أقصى تخصيص للصفقة<b>5 USDT</b></div><div>الاحتياطي النقدي<b>20%</b></div>
</div><p class="muted">لا تمنع هذه الحدود الخسائر أو فجوات الأسعار أو تأخر GitHub Actions. لا تستخدمها لحماية أموال حقيقية.</p></section>
<section class="panel"><h2>المراكز المفتوحة</h2><div class="scroll"><table><thead><tr><th>الأصل</th><th>الكمية</th><th>الدخول</th><th>السعر المرجعي</th><th>القيمة</th></tr></thead><tbody>{position_rows}</tbody></table></div></section>
<section class="panel"><h2>آخر الصفقات</h2><div class="scroll"><table><thead><tr><th>الدورة</th><th>الأصل</th><th>الجانب</th><th>سعر التنفيذ</th><th>صافي النتيجة</th><th>الوقت</th></tr></thead><tbody>{fill_rows}</tbody></table></div></section>
<footer>الجدولة كل خمس دقائق هي طلب GitHub Actions وليست ضماناً. لا يوجد محرك متصل 24/7. هذا التقرير لا يقدم نصيحة استثمارية ولا يضمن الربح.</footer>
</main></body></html>"""


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
