from __future__ import annotations

import argparse
import html
import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
)
INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
SPREADS = ("5", "10", "20", "30")
ABLATIONS = ((True, True), (True, False), (False, True), (False, False))
NO_LOSS_SENTINEL = Decimal("999999")
ZERO = Decimal("0")


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric field {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite numeric field {field}: {value!r}")
    return result


def decimal_text(value: Decimal, places: int = 6) -> str:
    quantum = Decimal("1").scaleb(-places)
    return format(value.quantize(quantum), "f")


def _series_metrics(item: dict[str, Any]) -> dict[str, Any]:
    folds = item.get("folds")
    if not isinstance(folds, list) or not folds:
        raise ValueError("Every symbol/timeframe/scenario must contain at least one fold.")

    returns = [decimal_value(fold.get("return_percent"), "return_percent") for fold in folds]
    drawdowns = [
        decimal_value(fold.get("max_drawdown_percent"), "max_drawdown_percent")
        for fold in folds
    ]
    fees = [decimal_value(fold.get("fees_paid"), "fees_paid") for fold in folds]
    reinvested = [
        decimal_value(fold.get("compounding_reinvested"), "compounding_reinvested")
        for fold in folds
    ]
    profit_factors: list[Decimal] = []
    for fold in folds:
        raw_pf = fold.get("profit_factor")
        if raw_pf is not None:
            profit_factors.append(decimal_value(raw_pf, "profit_factor"))

    return {
        "symbol": str(item["symbol"]),
        "interval": str(item["interval"]),
        "spread_bps": str(item["spread_assumption_bps"]),
        "shield_enabled": bool(item["shield_enabled"]),
        "compounding_enabled": bool(item["compounding_enabled"]),
        "closed_candles": int(item["closed_candles"]),
        "fold_count": len(folds),
        "positive_folds": sum(value > ZERO for value in returns),
        "negative_folds": sum(value < ZERO for value in returns),
        "zero_folds": sum(value == ZERO for value in returns),
        "mean_net_return_percent": decimal_text(
            sum(returns, ZERO) / Decimal(len(returns))
        ),
        "closed_trades": sum(int(fold.get("closed_trades", 0)) for fold in folds),
        "wins": sum(int(fold.get("wins", 0)) for fold in folds),
        "losses": sum(int(fold.get("losses", 0)) for fold in folds),
        "fees_paid_usdt": sum(fees, ZERO),
        "compounding_reinvested_usdt": sum(reinvested, ZERO),
        "max_drawdown_percent": max(drawdowns),
        "profit_factors": profit_factors,
    }


def build_summary(input_dir: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
    root = Path(input_dir)
    paths = sorted(root.glob("smart-sniper-walk-forward-*.json"))
    if len(paths) != len(INTERVALS):
        raise ValueError(
            f"Expected exactly {len(INTERVALS)} timeframe JSON files; found {len(paths)}."
        )

    all_series: dict[tuple[str, str, str, bool, bool], dict[str, Any]] = {}
    limitations: set[str] = set()
    generated_dates: list[datetime] = []
    sources: set[str] = set()

    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        interval = path.stem.removeprefix("smart-sniper-walk-forward-")
        if interval not in INTERVALS or payload.get("source") != "BINANCE_PUBLIC_MARKET_DATA":
            raise ValueError(f"Unexpected research file or data source: {path.name}.")
        if payload.get("research_universe") != list(SYMBOLS):
            raise ValueError(f"Unexpected symbol universe in {path.name}.")
        if [str(value) for value in payload.get("spread_scenarios_bps", [])] != list(SPREADS):
            raise ValueError(f"Unexpected spread scenarios in {path.name}.")
        sources.add(str(payload["source"]))
        limitations.update(str(value) for value in payload.get("limitations", []))
        generated_at = datetime.fromisoformat(
            str(payload["generated_at_utc"]).replace("Z", "+00:00")
        )
        generated_dates.append(generated_at.astimezone(timezone.utc))

        for item in payload.get("results", []):
            if item.get("interval") != interval:
                raise ValueError(f"Interval mismatch inside {path.name}.")
            symbol = str(item.get("symbol"))
            spread = str(item.get("spread_assumption_bps"))
            shield = bool(item.get("shield_enabled"))
            compounding = bool(item.get("compounding_enabled"))
            key = (symbol, interval, spread, shield, compounding)
            if symbol not in SYMBOLS or spread not in SPREADS:
                raise ValueError(f"Unexpected research series: {key!r}")
            if (shield, compounding) not in ABLATIONS:
                raise ValueError(f"Unexpected ablation flags: {key!r}")
            if key in all_series:
                raise ValueError(f"Duplicate research series: {key!r}")
            all_series[key] = _series_metrics(item)

    expected = {
        (symbol, interval, spread, shield, compounding)
        for symbol in SYMBOLS
        for interval in INTERVALS
        for spread in SPREADS
        for shield, compounding in ABLATIONS
    }
    missing = sorted(expected - set(all_series))
    extra = sorted(set(all_series) - expected)
    if missing or extra or len(all_series) != 768:
        raise ValueError(
            f"Incomplete research matrix: observed={len(all_series)}, "
            f"expected=768, missing={len(missing)}, extra={len(extra)}."
        )
    if len(sources) != 1:
        raise ValueError("Research files do not share one consistent data source.")

    rows: list[dict[str, Any]] = []
    for shield, compounding in ABLATIONS:
        for spread in SPREADS:
            series = [
                item for key, item in all_series.items()
                if key[2] == spread and key[3] is shield and key[4] is compounding
            ]
            if len(series) != len(SYMBOLS) * len(INTERVALS):
                raise ValueError("Each ablation/spread row must cover 48 independent series.")

            returns = [
                decimal_value(item["mean_net_return_percent"], "series_return")
                for item in series
            ]
            fold_count = sum(item["fold_count"] for item in series)
            pf_values = [value for item in series for value in item["profit_factors"]]
            ordinary_pf = [
                value for value in pf_values if ZERO < value < NO_LOSS_SENTINEL
            ]
            rows.append({
                "shield_enabled": shield,
                "compounding_enabled": compounding,
                "configuration": (
                    f"Shield {'ON' if shield else 'OFF'} · "
                    f"Compounding {'ON' if compounding else 'OFF'}"
                ),
                "spread_bps": spread,
                "series_count": len(series),
                "positive_series_count": sum(value > ZERO for value in returns),
                "fold_count": fold_count,
                "positive_folds": sum(item["positive_folds"] for item in series),
                "negative_folds": sum(item["negative_folds"] for item in series),
                "zero_folds": sum(item["zero_folds"] for item in series),
                "mean_net_return_percent": decimal_text(
                    sum(returns, ZERO) / Decimal(len(returns))
                ),
                "closed_trades": sum(item["closed_trades"] for item in series),
                "wins": sum(item["wins"] for item in series),
                "losses": sum(item["losses"] for item in series),
                "fees_paid_usdt": decimal_text(
                    sum((item["fees_paid_usdt"] for item in series), ZERO), 4
                ),
                "compounding_reinvested_usdt": decimal_text(
                    sum((item["compounding_reinvested_usdt"] for item in series), ZERO), 4
                ),
                "max_fold_drawdown_percent": decimal_text(
                    max((item["max_drawdown_percent"] for item in series), default=ZERO), 4
                ),
                "profit_factor": {
                    "median_finite_non_sentinel": (
                        decimal_text(statistics.median(ordinary_pf), 4)
                        if ordinary_pf else None
                    ),
                    "finite_fold_count": len(ordinary_pf),
                    "zero_fold_count": sum(value == ZERO for value in pf_values),
                    "no_loss_sentinel_count": sum(
                        value >= NO_LOSS_SENTINEL for value in pf_values
                    ),
                    "not_reported_fold_count": fold_count - len(pf_values),
                },
            })

    candle_coverage: dict[str, dict[str, int]] = {}
    for interval in INTERVALS:
        values = [
            item["closed_candles"] for key, item in all_series.items()
            if key[1] == interval and key[2] == "5" and key[3] is True and key[4] is True
        ]
        candle_coverage[interval] = {
            "series": len(values),
            "minimum_closed_candles": min(values),
            "maximum_closed_candles": max(values),
        }

    series_results = []
    for key in sorted(all_series):
        item = all_series[key]
        series_results.append({
            "symbol": item["symbol"],
            "interval": item["interval"],
            "spread_bps": item["spread_bps"],
            "shield_enabled": item["shield_enabled"],
            "compounding_enabled": item["compounding_enabled"],
            "closed_candles": item["closed_candles"],
            "fold_count": item["fold_count"],
            "positive_folds": item["positive_folds"],
            "negative_folds": item["negative_folds"],
            "zero_folds": item["zero_folds"],
            "mean_net_return_percent": item["mean_net_return_percent"],
            "closed_trades": item["closed_trades"],
            "fees_paid_usdt": decimal_text(item["fees_paid_usdt"], 6),
            "compounding_reinvested_usdt": decimal_text(
                item["compounding_reinvested_usdt"], 6
            ),
            "max_drawdown_percent": decimal_text(item["max_drawdown_percent"], 6),
        })

    all_means = [
        decimal_value(row["mean_net_return_percent"], "group_return") for row in rows
    ]
    if all(value <= ZERO for value in all_means):
        assessment = (
            "لم يظهر متوسط عائد صافٍ موجب لأي تجميع تكوين/سبريد؛ "
            "لا توجد أدلة على Positive Alpha مستقرة في هذه العينة."
        )
    else:
        assessment = (
            "ظهرت متوسطات موجبة في بعض التجميعات؛ لكنها لا تثبت وحدها "
            "وجود Alpha مستقرة أو قابلة للتكرار."
        )

    limitations.update({
        "لا تتضمن الشموع التاريخية عمق دفتر الأوامر؛ فروض السبريد 5/10/20/30 bps وليست أسعار bid/ask تاريخية.",
        "يُطبّق انزلاق محاكى 2 bps لكل جانب ورسوم 0.10% لكل جانب؛ متوسطات العائد صافية من هذه التكاليف.",
        "تُقيّم عتبات SL/TP عند إغلاق الشمعة؛ لا يعاد بناء المسار داخل الشمعة.",
        "كل نافذة/زوج/إطار زمني/فرض سبريد سلسلة مستقلة؛ مجموع الصفقات والرسوم ليس حساباً واحداً.",
        "يستخدم البحث رصيداً افتراضياً 70 USDT واحتياطياً 14 USDT وتذكرة أساسية 1.02 USDT وحداً أقصى للتذكرة 5 USDT.",
        "تتوفر 10,000 شمعة للأطر 1m/5m/15m/1h/4h؛ بيانات 1d أقل وتختلف تغطيتها بين الأزواج.",
        "النتائج التاريخية لا تضمن الأداء المستقبلي، ولا تمثل تداولاً حقيقياً أو توصية استثمارية.",
    })

    return {
        "schema_version": 1,
        "status": "completed",
        "generated_at_utc": max(generated_dates).isoformat(),
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": next(iter(sources)),
        "source_run_id": str(metadata["source_run_id"]),
        "source_run_number": int(metadata["source_run_number"]),
        "source_run_url": str(metadata["source_run_url"]),
        "source_commit": str(metadata["source_commit"]),
        "source_branch": str(metadata["source_branch"]),
        "research_universe": {
            "symbols": list(SYMBOLS),
            "intervals": list(INTERVALS),
            "spread_scenarios_bps": list(SPREADS),
            "ablation_configurations": [
                {"shield_enabled": shield, "compounding_enabled": compounding}
                for shield, compounding in ABLATIONS
            ],
        },
        "coverage": {
            "expected_series": 768,
            "observed_series": len(all_series),
            "total_folds": sum(item["fold_count"] for item in all_series.values()),
            "series_per_configuration_and_spread": 48,
            "candle_coverage_by_interval": candle_coverage,
        },
        "methodology": {
            "initial_cash_usdt": "70.00",
            "cash_reserve_usdt": "14.00",
            "base_ticket_usdt": "1.02",
            "maximum_ticket_usdt": "5.00",
            "fee_rate_per_side_percent": "0.10",
            "slippage_bps_per_side": "2",
            "stop_loss_percent": "0.50",
            "target_selection": "2.5R or 3R, selected by the fixed strategy rule",
            "spread_scenarios_bps": list(SPREADS),
            "return_basis": "net of entry/exit fees, spread assumption and simulated slippage",
        },
        "assessment": assessment,
        "limitations": sorted(limitations),
        "summary_rows": rows,
        "series_results": series_results,
    }


def render_html(summary: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    run_url = esc(summary["source_run_url"])
    commit_url = (
        "https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/commit/"
        + esc(summary["source_commit"])
    )
    json_url = (
        "https://raw.githubusercontent.com/IskanderKhemiri1234/Mumin-AI-Trader/"
        "mumin-paper-ledger/latest-research-summary.json"
    )
    rows = []
    for row in summary["summary_rows"]:
        pf = row["profit_factor"]
        if pf["median_finite_non_sentinel"] is None:
            pf_text = "غير متاح"
        else:
            pf_text = (
                f"وسيط {esc(pf['median_finite_non_sentinel'])} "
                f"(n={pf['finite_fold_count']})"
            )
        pf_text += (
            f"<small>صفر: {pf['zero_fold_count']} · "
            f"Sentinel: {pf['no_loss_sentinel_count']}</small>"
        )
        rows.append(
            "<tr>"
            f"<td>{esc(row['configuration'])}</td>"
            f"<td>{esc(row['spread_bps'])} bps</td>"
            f"<td>{esc(row['mean_net_return_percent'])}%</td>"
            f"<td>{row['positive_folds']}/{row['fold_count']}</td>"
            f"<td>{row['positive_series_count']}/{row['series_count']}</td>"
            f"<td>{row['closed_trades']}</td>"
            f"<td>{esc(row['fees_paid_usdt'])}</td>"
            f"<td>{esc(row['compounding_reinvested_usdt'])}</td>"
            f"<td>{pf_text}</td>"
            f"<td>{esc(row['max_fold_drawdown_percent'])}%</td>"
            "</tr>"
        )

    coverage = summary["coverage"]
    candles = coverage["candle_coverage_by_interval"]
    candle_text = " · ".join(
        f"{esc(interval)}: {candles[interval]['minimum_closed_candles']:,}–"
        f"{candles[interval]['maximum_closed_candles']:,}"
        for interval in INTERVALS
    )
    limitations = "".join(f"<li>{esc(item)}</li>" for item in summary["limitations"])
    assessment = esc(summary["assessment"])
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#071713">
<title>Mumin AI Trader | البحث التاريخي</title>
<style>
:root{{--bg:#071713;--panel:#10271e;--line:#31513e;--gold:#e5c982;--text:#f5f3e9;--muted:#a8bbae}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 80% -10%,#1b4832 0,transparent 45%),var(--bg);color:var(--text);font:14px/1.75 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1200px;margin:auto;padding:20px 14px 44px}}a{{color:var(--gold);text-decoration:underline;text-underline-offset:3px}}
header,.panel{{border:1px solid var(--line);border-radius:20px;background:linear-gradient(145deg,#153629,#0b2119);padding:18px;margin-bottom:14px;box-shadow:0 12px 36px #0003}}
.brand{{color:var(--gold);font-size:11px;font-weight:800;letter-spacing:2px}}h1{{font-size:clamp(22px,4vw,34px);margin:7px 0}}h2{{font-size:17px;color:var(--gold);margin:0 0 12px}}
.muted,small{{color:var(--muted)}}.warning{{padding:13px;border:1px solid #927b49;border-radius:13px;background:#302a1c;color:#f2dfad;margin-top:12px}}
.meta{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}.pill{{border:1px solid var(--line);border-radius:99px;padding:4px 10px;color:var(--muted);font-size:11px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}}.card{{background:#091d16;border:1px solid var(--line);border-radius:14px;padding:12px;min-width:0}}
.card span{{display:block;color:var(--muted);font-size:11px}}.card b{{display:block;color:var(--gold);font-size:19px;overflow-wrap:anywhere}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:13px}}table{{border-collapse:collapse;width:100%;min-width:1050px;font-size:12px}}
th,td{{padding:10px;border-bottom:1px solid #294737;text-align:right;vertical-align:top;white-space:nowrap}}th{{background:#183526;color:var(--gold)}}td small{{display:block;white-space:normal;font-size:10px}}
.note{{color:var(--muted);font-size:12px}}.assessment{{border-inline-start:3px solid var(--gold);padding:10px 13px;background:#0a1d16;border-radius:8px}}
ul{{padding-inline-start:22px}}li{{margin:5px 0}}footer{{text-align:center;color:var(--muted);font-size:11px;padding:14px}}
@media(max-width:700px){{main{{padding:12px 10px 30px}}.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}header,.panel{{padding:14px}}}}
</style>
</head>
<body><main>
<header>
<div class="brand">MUMIN · SMART SNIPER · PAPER ONLY</div>
<h1>ملخص البحث التاريخي خارج العينة</h1>
<p class="muted">ملخص قراءة فقط لأربعة تكوينات Ablation، مع اختبارات حساسية السبريد وتغطية الأزواج والأطر الزمنية.</p>
<div class="meta">
<span class="pill">Run #{summary['source_run_number']}</span>
<span class="pill">8 أزواج · 6 أطر · 4 سبريد · 4 تكوينات</span>
<span class="pill">تغطية {coverage['observed_series']}/{coverage['expected_series']} سلسلة</span>
</div>
<p class="muted">وقت توليد البيانات: {esc(summary['generated_at_utc'])} UTC<br>
وقت النشر: {esc(summary['published_at_utc'])} UTC<br>
الفرع: {esc(summary['source_branch'])} · Commit: <a href="{commit_url}">{esc(summary['source_commit'][:12])}</a></p>
<p><a href="{run_url}">فتح تشغيل GitHub Actions المصدر</a> · <a href="{json_url}">فتح ملف JSON الكامل</a></p>
<div class="warning"><b>Paper فقط — لا توجد أوامر تداول حقيقية.</b> هذه نتائج تاريخية افتراضية وليست أرباحاً قابلة للسحب أو ضماناً للمستقبل. لا تُستخدم كإشارة شراء مباشرة.</div>
</header>
<section class="panel">
<h2>التغطية والمنهجية</h2>
<div class="cards">
<div class="card"><span>السلاسل المستقلة</span><b>{coverage['observed_series']}</b></div>
<div class="card"><span>نوافذ الاختبار</span><b>{coverage['total_folds']:,}</b></div>
<div class="card"><span>رأس مال كل سلسلة</span><b>70 USDT</b></div>
<div class="card"><span>تذكرة / احتياطي</span><b>5 / 14 USDT</b></div>
</div>
<p class="note">الشموع المغلقة حسب الإطار: {candle_text}</p>
<p class="note">التكلفة: رسوم 0.10% لكل جانب + انزلاق 2 bps لكل جانب + فرض السبريد المحدد. العائد المعروض صافٍ من هذه التكاليف.</p>
</section>
<section class="panel">
<h2>نتائج التكوينات الأربعة عبر فروض السبريد</h2>
<p class="assessment">{assessment}</p>
<p class="note">كل صف يلخّص 48 سلسلة مستقلة (8 أزواج × 6 أطر) ضمن فرض سبريد واحد. متوسط العائد هو متوسط عوائد النوافذ، وليس عائداً مركباً لحساب واحد. أعداد الصفقات والرسوم مجاميع وصفية عبر سلاسل منفصلة، ولا تمثل محفظة موحدة.</p>
<div class="table-wrap"><table>
<thead><tr><th>التكوين</th><th>السبريد</th><th>متوسط صافي العائد/نافذة</th><th>النوافذ الموجبة</th><th>السلاسل الموجبة</th><th>الصفقات</th><th>الرسوم USDT</th><th>إعادة الاستثمار USDT</th><th>Profit Factor*</th><th>أقصى سحب لنافذة</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="note">* Profit Factor: الوسيط محسوب فقط للنوافذ ذات PF محدود وموجب. القيمة 999999 في المصدر sentinel عند غياب الخسائر، والصفر يظهر في نوافذ بلا أرباح؛ لذلك لا يوجد PF موحد موثوق ولا ينبغي تفسير sentinel كأداء هائل.</p>
</section>
<section class="panel">
<h2>القيود والتحذيرات</h2>
<ul>{limitations}</ul>
<p class="note">يُقيّم SL/TP عند إغلاق الشمعة، وليس من مسار intrabar. فروض السبريد ليست order book تاريخياً. لا يوجد نموذج ML أو تقدير احتمالات مُعاير. العينة لا تثبت Positive Alpha مستقرة.</p>
</section>
<footer>لوحة Mumin AI Trader · للقراءة فقط · لا مفاتيح API ولا صلاحيات تداول</footer>
</main></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and publish a read-only Smart Sniper walk-forward summary."
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-html", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-run-number", required=True, type=int)
    parser.add_argument("--source-run-url", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-branch", required=True)
    args = parser.parse_args()

    summary = build_summary(args.input_dir, {
        "source_run_id": args.source_run_id,
        "source_run_number": args.source_run_number,
        "source_run_url": args.source_run_url,
        "source_commit": args.source_commit,
        "source_branch": args.source_branch,
    })
    json_path, html_path = Path(args.output_json), Path(args.output_html)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(render_html(summary), encoding="utf-8")
    print(
        f"Validated {summary['coverage']['observed_series']} independent series "
        f"and {summary['coverage']['total_folds']} out-of-sample windows."
    )


if __name__ == "__main__":
    main()
