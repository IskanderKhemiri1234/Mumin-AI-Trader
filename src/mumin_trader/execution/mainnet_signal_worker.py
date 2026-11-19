from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import uuid

import httpx
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from config.config import Settings, get_settings
from mumin_trader.ai_engine.strategy_engine import CORE_SYMBOLS
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient
from mumin_trader.exchanges.binance.spot_mainnet_execution import (
    BinanceSpotMainnetExecution,
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)
from mumin_trader.market_scanner.scanner import MarketScanner


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AUTONOMOUS_CONFIRMATION = "I_ACKNOWLEDGE_AUTONOMOUS_SPOT_EXECUTION"
DEFAULT_INTERVAL_SECONDS = 60.0


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _sqlite_path(value: str, *, root: Path = PROJECT_ROOT) -> Path:
    normalized = value.removeprefix("sqlite:///") if value.startswith("sqlite:///") else value
    if "://" in normalized or normalized == ":memory:":
        raise ValueError("Mainnet worker requires a durable local SQLite database.")
    path = Path(normalized).expanduser()
    return path if path.is_absolute() else root / path


class MainnetSignalLedger:
    """Durable signal, proposal and worker-audit ledger; never stores credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS mainnet_signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    candle_bucket TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    entry_price TEXT NOT NULL,
                    stop_loss_price TEXT,
                    take_profit_price TEXT,
                    filter_reasons_json TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    order_list_client_id TEXT,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, candle_bucket)
                );
                CREATE TABLE IF NOT EXISTS mainnet_worker_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mainnet_worker_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    desired_state TEXT NOT NULL DEFAULT 'RUNNING',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mainnet_worker_runtime (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    supervisor_pid INTEGER,
                    worker_pid INTEGER,
                    supervisor_heartbeat_at TEXT,
                    worker_heartbeat_at TEXT,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO mainnet_worker_control(id, desired_state, updated_at)
                VALUES (1, 'RUNNING', '1970-01-01T00:00:00+00:00');
                INSERT OR IGNORE INTO mainnet_worker_runtime(id, updated_at)
                VALUES (1, '1970-01-01T00:00:00+00:00');
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def audit(self, event_type: str, actor: str, detail: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO mainnet_worker_audit(event_type,actor,detail_json,created_at) VALUES(?,?,?,?)",
                (event_type, actor, json.dumps(detail, sort_keys=True, default=str), self._now()),
            )

    def desired_state(self) -> str:
        with self._connect() as db:
            row = db.execute(
                "SELECT desired_state FROM mainnet_worker_control WHERE id=1"
            ).fetchone()
        return str(row["desired_state"]) if row else "STOPPED"

    def set_desired_state(self, desired_state: str, actor: str) -> None:
        if desired_state not in {"RUNNING", "STOPPED"}:
            raise ValueError("Mainnet signal worker desired state must be RUNNING or STOPPED.")
        with self._connect() as db:
            db.execute(
                "UPDATE mainnet_worker_control SET desired_state=?,updated_at=? WHERE id=1",
                (desired_state, self._now()),
            )
        self.audit("MAINNET_SIGNAL_DESIRED_STATE_CHANGED", actor, {"desired_state": desired_state})

    def supervisor_heartbeat(self, pid: int, worker_pid: int | None) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute(
                """UPDATE mainnet_worker_runtime
                   SET supervisor_pid=?,worker_pid=?,supervisor_heartbeat_at=?,updated_at=?
                   WHERE id=1""",
                (pid, worker_pid, now, now),
            )

    def worker_heartbeat(self, pid: int) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute(
                """UPDATE mainnet_worker_runtime
                   SET worker_pid=?,worker_heartbeat_at=?,updated_at=? WHERE id=1""",
                (pid, now, now),
            )

    def record_candidate(self, candidate: Any, bucket: str, disposition: str, detail: str = "") -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO mainnet_signal_events
                   (symbol,candle_bucket,signal,score,entry_price,stop_loss_price,
                    take_profit_price,filter_reasons_json,disposition,detail,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol,candle_bucket) DO UPDATE SET
                     signal=excluded.signal,
                     score=excluded.score,
                     entry_price=excluded.entry_price,
                     stop_loss_price=excluded.stop_loss_price,
                     take_profit_price=excluded.take_profit_price,
                     filter_reasons_json=excluded.filter_reasons_json,
                     disposition=excluded.disposition,
                     detail=excluded.detail
                   WHERE mainnet_signal_events.order_list_client_id IS NULL
                     AND (
                       mainnet_signal_events.signal<>excluded.signal
                       OR mainnet_signal_events.score<>excluded.score
                       OR mainnet_signal_events.entry_price<>excluded.entry_price
                       OR mainnet_signal_events.stop_loss_price IS NOT excluded.stop_loss_price
                       OR mainnet_signal_events.take_profit_price IS NOT excluded.take_profit_price
                       OR mainnet_signal_events.filter_reasons_json<>excluded.filter_reasons_json
                       OR mainnet_signal_events.disposition<>excluded.disposition
                     )""",
                (
                    candidate.symbol,
                    bucket,
                    candidate.signal,
                    int(candidate.strategy_score),
                    str(candidate.last_price),
                    str(candidate.stop_loss_price) if candidate.stop_loss_price is not None else None,
                    str(candidate.take_profit_price) if candidate.take_profit_price is not None else None,
                    json.dumps(list(candidate.filter_reasons)),
                    disposition,
                    detail[:1000],
                    self._now(),
                ),
            )
            return cursor.rowcount == 1

    def claim_submission(self, symbol: str, bucket: str, client_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE mainnet_signal_events
                   SET disposition='SUBMISSION_STARTED',order_list_client_id=?,detail=?
                   WHERE symbol=? AND candle_bucket=?
                     AND order_list_client_id IS NULL AND disposition='SHADOW_PROPOSAL'""",
                (client_id, "Durable one-attempt-per-symbol-per-candle claim.", symbol, bucket),
            )
            return cursor.rowcount == 1

    def finish_submission(self, symbol: str, bucket: str, state: str, error: str) -> None:
        if state not in {"SUBMISSION_REJECTED", "OUTCOME_UNKNOWN"}:
            raise ValueError("Unsupported Mainnet signal submission result.")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE mainnet_signal_events SET disposition=?,detail=?
                   WHERE symbol=? AND candle_bucket=? AND disposition='SUBMISSION_STARTED'
                     AND order_list_client_id IS NOT NULL""",
                (state, error[:1000], symbol, bucket),
            )
        if cursor.rowcount:
            self.audit(
                "MAINNET_SIGNAL_SUBMISSION_FINISHED",
                "mainnet-signal-worker",
                {"symbol": symbol, "candle_bucket": bucket, "state": state, "error": error[:500]},
            )

    def attach_order(self, symbol: str, bucket: str, client_id: str, detail: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE mainnet_signal_events
                   SET disposition='ORDER_SUBMITTED', order_list_client_id=?, detail=?
                   WHERE symbol=? AND candle_bucket=? AND order_list_client_id=?""",
                (client_id, detail[:1000], symbol, bucket, client_id),
            )


class MainnetSignalWorker:
    """Independent Mainnet signal process; Paper worker and Paper ledger are never imported."""

    def __init__(
        self,
        settings: Settings,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        ledger_path: Path | None = None,
        autonomous: bool | None = None,
    ) -> None:
        if interval_seconds < 15:
            raise ValueError("Mainnet signal scan interval cannot be less than 15 seconds.")
        self.settings = settings
        self.interval_seconds = interval_seconds
        configured = os.getenv("MAINNET_SIGNAL_DATABASE_URL", "sqlite:///./data/mumin_mainnet_signals.db")
        self.ledger = MainnetSignalLedger(ledger_path or _sqlite_path(configured))
        self.autonomous = (
            _enabled(os.getenv("MAINNET_AUTONOMOUS_EXECUTION_ENABLED"))
            if autonomous is None else autonomous
        )
        if self.autonomous:
            if settings.TRADING_ENV.value != "MAINNET" or not settings.MAINNET_SPOT_EXECUTION_ENABLED:
                raise RuntimeError("Autonomous mode requires the separately gated Mainnet Spot environment.")
            if os.getenv("MAINNET_AUTONOMOUS_CONFIRMATION", "") != AUTONOMOUS_CONFIRMATION:
                raise RuntimeError("Autonomous Mainnet execution requires its separate exact confirmation phrase.")
        self.actor = f"mainnet-signal-worker-{os.getpid()}"
        requested_symbols = {
            item.strip().upper()
            for item in os.getenv("MAINNET_SIGNAL_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,LINKUSDT").split(",")
            if item.strip()
        }
        allowed_symbols = requested_symbols.intersection(settings.watchlist_symbols).intersection(CORE_SYMBOLS)
        if not allowed_symbols:
            raise ValueError("Mainnet signal symbols must be in the configured Spot watchlist.")
        entry_interval = os.getenv("MAINNET_SIGNAL_INTERVAL", "15m").strip().lower()
        self.scanner = MarketScanner(
            allowed_symbols=allowed_symbols,
            quote_asset=settings.QUOTE_ASSET,
            min_quote_volume=Decimal("1000000"),
            max_spread_bps=Decimal("8"),
            interval=entry_interval,
            candle_limit=100,
            max_symbols=len(allowed_symbols),
            min_expected_move_bps=Decimal("35"),
        )
        # Strategy scanning uses the fixed Binance public Mainnet host, with no API key.
        # This remains safe in SHADOW mode even while all private execution gates are closed.
        self._market_http = httpx.AsyncClient(
            base_url="https://api.binance.com",
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json"},
        )
        self.market = BinanceSpotRESTClient(settings, client=self._market_http)
        self.order_store: SQLiteOrderStore | None = None
        self.execution: BinanceSpotMainnetExecution | None = None
        if self.autonomous:
            database_path = _sqlite_path(settings.MAINNET_ORDER_DATABASE_URL)
            self.order_store = SQLiteOrderStore(str(database_path))
            self.execution = BinanceSpotMainnetExecution(settings, self.order_store)
        self.stop_requested = False

    def request_stop(self, _signum: int, _frame: object) -> None:
        self.stop_requested = True

    def _interval_milliseconds(self) -> int:
        interval = self.scanner.interval
        if interval.endswith("m"):
            return int(interval[:-1]) * 60_000
        if interval.endswith("h"):
            return int(interval[:-1]) * 3_600_000
        raise ValueError("Unsupported Mainnet signal candle interval.")

    @staticmethod
    def _select_entry_candidate(candidates: list[Any]) -> Any | None:
        if not candidates:
            return None
        return min(candidates, key=lambda item: (-int(item.strategy_score), item.symbol))

    @staticmethod
    def _bucket(signal_candle_open_time_ms: int) -> str:
        if signal_candle_open_time_ms <= 0:
            raise ValueError("Mainnet signal is missing the last closed candle timestamp.")
        return datetime.fromtimestamp(
            signal_candle_open_time_ms / 1000,
            tz=timezone.utc,
        ).isoformat()

    @staticmethod
    def _floor_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            raise ValueError("Exchange LOT_SIZE stepSize is invalid.")
        return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

    @staticmethod
    def _floor_tick(value: Decimal, tick: Decimal) -> Decimal:
        if tick <= 0:
            raise ValueError("Exchange PRICE_FILTER tickSize is invalid.")
        return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick

    async def _submit_protected(self, candidate: Any, bucket: str) -> bool:
        if self.execution is None:
            raise RuntimeError("Execution adapter is unavailable in SHADOW mode.")
        symbol = candidate.symbol
        if candidate.stop_loss_price is None or candidate.take_profit_price is None or candidate.last_price <= 0:
            raise SpotExecutionError("Strategy approved the signal without complete entry/exit price levels.")
        strategy_stop_percent = (candidate.last_price - candidate.stop_loss_price) / candidate.last_price * Decimal("100")
        strategy_target_percent = (candidate.take_profit_price - candidate.last_price) / candidate.last_price * Decimal("100")
        if (
            abs(strategy_stop_percent - Decimal("0.50")) > Decimal("0.02")
            or abs(strategy_target_percent - Decimal("1.25")) > Decimal("0.02")
        ):
            raise SpotExecutionError("Strategy exit levels do not match the protected Mainnet risk policy.")
        metadata = await self.execution._exchange_symbol(symbol)
        filters = {
            item.get("filterType"): item
            for item in metadata.get("filters", [])
            if isinstance(item, dict)
        }
        lot = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        step = Decimal(str(lot.get("stepSize", "0")))
        tick = Decimal(str(price_filter.get("tickSize", "0")))
        working = self._floor_tick(candidate.bid, tick)
        if working <= 0:
            raise SpotExecutionError("Strategy candidate has no valid executable bid price.")
        take_profit = self._floor_tick(working * Decimal("1.0125"), tick)
        stop = self._floor_tick(working * Decimal("0.995"), tick)
        stop_limit = self._floor_tick(stop * Decimal("0.999"), tick)
        if not (take_profit > candidate.last_price > stop and working < candidate.last_price):
            raise SpotExecutionError("Live quote moved outside the strategy's protected-entry price envelope.")
        max_risk_usd = (
            self.settings.MAX_CAPITAL_ALLOCATION_USD
            * self.settings.MAX_RISK_PER_TRADE_PERCENT / Decimal("100")
        )
        risk_limited_quantity = max_risk_usd / (working - stop)
        allocation_limited_quantity = self.settings.MAX_TRADE_ALLOCATION_USD / working
        quantity = self._floor_step(min(risk_limited_quantity, allocation_limited_quantity), step)
        notional = quantity * working
        estimated_risk = quantity * (working - stop)
        if quantity <= 0 or notional < self.settings.MIN_TRADE_ALLOCATION_USD:
            raise SpotExecutionError("Risk and allocation limits cannot satisfy the exchange minimum notional.")
        if notional > self.settings.MAX_TRADE_ALLOCATION_USD or estimated_risk > max_risk_usd:
            raise SpotExecutionError("Protected entry exceeds configured allocation or per-trade risk.")

        list_id = "m" + uuid.uuid4().hex
        working_id = "m" + uuid.uuid4().hex
        above_id = "m" + uuid.uuid4().hex
        below_id = "m" + uuid.uuid4().hex
        if not self.ledger.claim_submission(symbol, bucket, list_id):
            self.ledger.audit(
                "SIGNAL_ALREADY_CLAIMED", self.actor,
                {"symbol": symbol, "candle_bucket": bucket, "order_list_client_id": list_id},
            )
            return False
        response = await self.execution.opoco.place_buy_opoco(
            symbol=symbol,
            quantity=quantity,
            working_price=working,
            take_profit_price=take_profit,
            stop_price=stop,
            stop_limit_price=stop_limit,
            list_client_order_id=list_id,
            working_client_order_id=working_id,
            pending_above_client_order_id=above_id,
            pending_below_client_order_id=below_id,
        )
        self.ledger.attach_order(
            symbol,
            bucket,
            list_id,
            json.dumps({"order_list_id": response.get("orderListId"), "status": response.get("listStatusType")}),
        )
        self.ledger.audit(
            "PROTECTED_OPOCO_SUBMITTED",
            self.actor,
            {"symbol": symbol, "order_list_client_id": list_id, "working_price": str(working),
             "quantity": str(quantity), "estimated_risk_usd": str(estimated_risk),
             "strategy_score": candidate.strategy_score},
        )
        return True

    async def run_once(self) -> dict[str, Any]:
        if self.autonomous:
            if self.execution is None or self.order_store is None:
                raise RuntimeError("Autonomous execution adapter was not initialized.")
            reconciliation = await self.execution.reconcile()
            halted, halt_reason = self.order_store.is_halted()
            if halted:
                self.ledger.audit(
                    "SCAN_BLOCKED_BY_MAINNET_HALT", self.actor,
                    {"reason": halt_reason, "discrepancies": reconciliation.get("discrepancies", [])[:10]},
                )
                return {"status": "HALTED", "reason": halt_reason, "signals": 0, "orders": 0}

        candidates = await self.scanner.scan(self.market)
        approved: list[Any] = []
        buckets: dict[str, str] = {}
        orders = 0
        recorded = 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = self._interval_milliseconds()
        for candidate in candidates:
            candle_open_ms = candidate.signal_candle_open_time_ms
            if candle_open_ms <= 0:
                self.ledger.audit(
                    "SIGNAL_REJECTED_MISSING_CANDLE_TIME", self.actor,
                    {"symbol": candidate.symbol, "interval": self.scanner.interval},
                )
                continue
            candle_age_ms = now_ms - candle_open_ms
            if candle_age_ms < interval_ms - 60_000 or candle_age_ms > (2 * interval_ms) + 60_000:
                self.ledger.audit(
                    "SIGNAL_REJECTED_STALE_CANDLE", self.actor,
                    {"symbol": candidate.symbol, "candle_open_time_ms": candle_open_ms,
                     "candle_age_ms": candle_age_ms, "interval": self.scanner.interval},
                )
                continue
            bucket = self._bucket(candle_open_ms)
            buckets[candidate.symbol] = bucket
            disposition = "SHADOW_PROPOSAL" if candidate.signal == "BUY_CANDIDATE" else "FILTERED"
            inserted = self.ledger.record_candidate(candidate, bucket, disposition)
            if candidate.signal == "BUY_CANDIDATE":
                approved.append(candidate)
            if inserted:
                recorded += 1
                self.ledger.audit(
                    "STRATEGY_SIGNAL_EVALUATED",
                    self.actor,
                    {"symbol": candidate.symbol, "signal": candidate.signal,
                     "score": candidate.strategy_score, "filters": list(candidate.filter_reasons),
                     "entry_price": str(candidate.last_price), "candle_bucket": bucket},
                )

        if self.autonomous and approved:
            ranked = sorted(approved, key=lambda item: (-int(item.strategy_score), item.symbol))
            candidate = self._select_entry_candidate(approved)
            assert candidate is not None
            if len(ranked) > 1:
                self.ledger.audit(
                    "MULTI_SIGNAL_PRIORITY_SELECTION", self.actor,
                    {
                        "selected_symbol": candidate.symbol,
                        "selected_score": candidate.strategy_score,
                        "eligible_symbols": [item.symbol for item in ranked],
                        "policy": "highest_score_one_entry_per_scan_cycle",
                    },
                )
            try:
                orders = int(await self._submit_protected(candidate, buckets[candidate.symbol]))
            except SpotOrderOutcomeUnknown as exc:
                self.ledger.finish_submission(
                    candidate.symbol, buckets[candidate.symbol], "OUTCOME_UNKNOWN", str(exc),
                )
                self.ledger.audit(
                    "PROTECTED_ENTRY_OUTCOME_UNKNOWN", self.actor,
                    {"symbol": candidate.symbol, "reason": str(exc)[:500]},
                )
            except SpotExecutionError as exc:
                self.ledger.finish_submission(
                    candidate.symbol, buckets[candidate.symbol], "SUBMISSION_REJECTED", str(exc),
                )
                self.ledger.audit(
                    "PROTECTED_ENTRY_REJECTED", self.actor,
                    {"symbol": candidate.symbol, "error_type": type(exc).__name__, "reason": str(exc)[:500]},
                )
            except Exception as exc:
                self.ledger.finish_submission(
                    candidate.symbol, buckets[candidate.symbol], "OUTCOME_UNKNOWN", str(exc),
                )
                self.ledger.audit(
                    "PROTECTED_ENTRY_OUTCOME_UNKNOWN", self.actor,
                    {"symbol": candidate.symbol, "error_type": type(exc).__name__, "reason": str(exc)[:500]},
                )
        self.ledger.audit(
            "SCAN_CYCLE_COMPLETED", self.actor,
            {"candidates": len(candidates), "approved": len(approved), "new_records": recorded,
             "orders_submitted": orders, "autonomous_mode": self.autonomous},
        )
        return {
            "status": "OK",
            "candidates": len(candidates),
            "approved": len(approved),
            "new_records": recorded,
            "orders_submitted": orders,
            "mode": "AUTONOMOUS_PROTECTED_OPOCO" if self.autonomous else "SHADOW_ONLY",
        }

    async def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)
        self.ledger.audit(
            "MAINNET_SIGNAL_WORKER_STARTED", self.actor,
            {"pid": os.getpid(), "mode": "AUTONOMOUS_PROTECTED_OPOCO" if self.autonomous else "SHADOW_ONLY",
             "interval_seconds": self.interval_seconds},
        )
        try:
            while not self.stop_requested and self.ledger.desired_state() == "RUNNING":
                started = asyncio.get_running_loop().time()
                try:
                    result = await self.run_once()
                    print(json.dumps(result, sort_keys=True), flush=True)
                except Exception as exc:
                    self.ledger.audit(
                        "SCAN_CYCLE_FAILED", self.actor,
                        {"error_type": type(exc).__name__, "reason": str(exc)[:500]},
                    )
                    print(f"Mainnet signal cycle failed closed: {type(exc).__name__}", flush=True)
                finally:
                    self.ledger.worker_heartbeat(os.getpid())
                elapsed = asyncio.get_running_loop().time() - started
                await asyncio.sleep(max(1.0, self.interval_seconds - elapsed))
        finally:
            self.ledger.audit("MAINNET_SIGNAL_WORKER_STOPPED", self.actor, {"pid": os.getpid()})
            await self.market.close()
            await self._market_http.aclose()
            if self.execution is not None:
                await self.execution.close()


async def _main() -> None:
    settings = get_settings()
    interval = float(os.getenv("MAINNET_SIGNAL_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    worker = MainnetSignalWorker(settings, interval_seconds=interval)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())
