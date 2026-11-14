from __future__ import annotations

import json

import pytest

from scripts.build_research_summary import (
    INTERVALS,
    SPREADS,
    SYMBOLS,
    build_summary,
)


def _write_complete_matrix(root):
    root.mkdir(parents=True, exist_ok=True)
    for interval in INTERVALS:
        results = []
        for symbol in SYMBOLS:
            for spread in SPREADS:
                for shield in (True, False):
                    for compounding in (True, False):
                        results.append({
                            "symbol": symbol,
                            "interval": interval,
                            "higher_timeframes": [],
                            "spread_assumption_bps": spread,
                            "shield_enabled": shield,
                            "compounding_enabled": compounding,
                            "closed_candles": 3000 if interval == "1d" else 10000,
                            "folds": [{
                                "start_index": 60,
                                "end_index": 80,
                                "ending_equity": "70.01",
                                "return_percent": "0.014285",
                                "max_drawdown_percent": "0.01",
                                "closed_trades": 1,
                                "wins": 1,
                                "losses": 0,
                                "win_rate_percent": "100",
                                "profit_factor": "999999",
                                "fees_paid": "0.002",
                                "shield_blocks": 0,
                                "buy_candidates": 1,
                                "blocked_buy_candidates": 0,
                                "entry_orders": 1,
                                "compounding_reinvested": "0.1" if compounding else "0",
                            }],
                        })
        payload = {
            "generated_at_utc": "2026-11-14T00:00:00+00:00",
            "source": "BINANCE_PUBLIC_MARKET_DATA",
            "research_universe": list(SYMBOLS),
            "method": "test",
            "spread_scenarios_bps": list(SPREADS),
            "results": results,
            "limitations": ["test limitation"],
        }
        (root / f"smart-sniper-walk-forward-{interval}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )


def _metadata():
    return {
        "source_run_id": "123",
        "source_run_number": 57,
        "source_run_url": "https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/123",
        "source_commit": "a" * 40,
        "source_branch": "feature/smart-sniper-validation-final",
    }


def test_build_summary_validates_full_matrix_and_keeps_series_separate(tmp_path):
    _write_complete_matrix(tmp_path)
    summary = build_summary(tmp_path, _metadata())

    assert summary["coverage"]["observed_series"] == 768
    assert summary["coverage"]["total_folds"] == 768
    assert len(summary["summary_rows"]) == 16
    assert len(summary["series_results"]) == 768
    assert summary["summary_rows"][0]["series_count"] == 48
    assert summary["summary_rows"][0]["positive_folds"] == 48
    assert summary["summary_rows"][0]["profit_factor"]["no_loss_sentinel_count"] == 48
    assert summary["assessment"].startswith("ظهرت متوسطات موجبة")


def test_build_summary_rejects_incomplete_matrix(tmp_path):
    _write_complete_matrix(tmp_path)
    path = tmp_path / "smart-sniper-walk-forward-1d.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["results"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Incomplete research matrix"):
        build_summary(tmp_path, _metadata())
