from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import tempfile
from pathlib import Path

from scripts.replay_paper_observations import _prepare_cycles, replay_archive


def run_walk_forward(
    archive_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    folds: int = 3,
) -> dict[str, Any]:
    archive = Path(archive_path)
    source_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    source_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    if source_manifest.get("archive_sha256") != source_sha:
        raise ValueError("Source archive SHA-256 does not match its manifest.")
    if not source_manifest.get("quality", {}).get("replay_ready", False):
        raise ValueError("Walk-forward refuses a dataset that is not replay_ready.")

    rows = []
    with gzip.open(archive, "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("record_type") == "market_snapshot":
                row = item.get("record")
                if row and row.get("data_source") == "BINANCE_PUBLIC_MARKET_DATA":
                    rows.append(row)
    cycles = _prepare_cycles(rows)
    if folds < 1:
        raise ValueError("folds must be at least one.")
    minimum_cycles = 30 + folds * 30
    if len(cycles) < minimum_cycles:
        raise ValueError(
            f"Need at least {minimum_cycles} cycles for {folds} folds "
            "with 30 warm-up and 30 evaluation cycles each."
        )

    available = len(cycles) - 30
    base_test_size = available // folds
    if base_test_size < 30:
        raise ValueError("Each forward evaluation fold must contain at least 30 cycles.")

    results = []
    with tempfile.TemporaryDirectory(prefix="mumin-paper-walk-forward-") as temporary:
        root = Path(temporary)
        for index in range(folds):
            test_start = 30 + index * base_test_size
            test_end = len(cycles) if index == folds - 1 else 30 + (index + 1) * base_test_size
            warmup_start = test_start - 30
            selected = cycles[warmup_start:test_end]
            selected_rows = [
                row for _, _, symbol_rows in selected for row in symbol_rows.values()
            ]
            sub_archive = root / f"fold-{index + 1}.jsonl.gz"
            sub_manifest = root / f"fold-{index + 1}.manifest.json"
            sub_output = root / f"fold-{index + 1}.result.json"
            with sub_archive.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                    for row in selected_rows:
                        record = {"record_type": "market_snapshot", "record": row}
                        zipped.write(
                            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
                        )
            sub_sha = hashlib.sha256(sub_archive.read_bytes()).hexdigest()
            sub_manifest.write_text(json.dumps({
                "archive_sha256": sub_sha,
                "quality": {"replay_ready": True},
                "parent_archive_sha256": source_sha,
            }), encoding="utf-8")
            replay = replay_archive(
                sub_archive, sub_manifest, sub_output,
                require_ready=True, warmup_cycles=30,
            )
            metrics = replay["metrics"]
            results.append({
                "fold": index + 1,
                "warmup_start_tick": selected[0][0],
                "evaluation_start_tick": selected[30][0],
                "evaluation_end_tick": selected[-1][0],
                "evaluation_cycles": metrics["evaluation_cycles"],
                "closed_trades": metrics["closed_trades"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "net_pnl_usdt": metrics["realized_net_pnl_usdt"],
                "return_percent": metrics["return_percent"],
                "fees_usdt": metrics["fees_usdt"],
                "profit_factor_net": metrics["profit_factor_net"],
                "max_drawdown_percent": metrics["max_drawdown_percent"],
                "accounting_status": metrics["accounting_status"],
                "per_symbol": metrics["per_symbol"],
            })

    returns = [float(item["return_percent"]) for item in results]
    total_trades = sum(int(item["closed_trades"]) for item in results)
    all_accounting_pass = all(item["accounting_status"] == "PASS" for item in results)
    result = {
        "schema_version": 1,
        "evaluation": "WALK_FORWARD_OOS",
        "strategy": "FROZEN_PUBLIC_PAPER_MICRO_ALPHA",
        "source_archive_sha256": source_sha,
        "folds": results,
        "aggregate": {
            "fold_count": len(results),
            "total_closed_trades": total_trades,
            "mean_fold_return_percent": statistics.mean(returns),
            "median_fold_return_percent": statistics.median(returns),
            "worst_fold_return_percent": min(returns),
            "positive_folds": sum(value > 0 for value in returns),
            "accounting_pass_all_folds": all_accounting_pass,
        },
        "tuning_performed": False,
        "holdout_used": False,
        "limitations": [
            "The same frozen strategy and cost model are replayed in every fold; no parameter fitting occurs.",
            "Each fold uses the preceding 30 observations only as indicator warm-up with new entries disabled.",
            "Walk-forward folds are development/validation evidence, not the final sealed Holdout.",
            "This result cannot authorize Mainnet execution.",
        ],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if not all_accounting_pass:
        raise RuntimeError("At least one fold failed accounting reconciliation.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Leakage-resistant walk-forward replay of public Paper snapshots.")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--folds", type=int, default=3)
    args = parser.parse_args()
    result = run_walk_forward(
        args.archive, args.manifest, args.output, folds=args.folds
    )
    print(json.dumps(result["aggregate"], indent=2))


if __name__ == "__main__":
    main()
