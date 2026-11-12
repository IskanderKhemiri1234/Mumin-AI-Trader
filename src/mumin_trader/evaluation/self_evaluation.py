from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PerformanceSummary:
    trade_count: int
    net_pnl: Decimal
    win_rate: Decimal
    max_drawdown: Decimal


def summarize(closed_trade_pnls: list[Decimal]) -> PerformanceSummary:
    if not closed_trade_pnls:
        return PerformanceSummary(0, Decimal("0"), Decimal("0"), Decimal("0"))
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    wins = 0
    for pnl in closed_trade_pnls:
        equity += pnl
        peak = max(peak, equity)
        wins += pnl > 0
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * Decimal("100"))
    return PerformanceSummary(len(closed_trade_pnls), equity, Decimal(wins) / Decimal(len(closed_trade_pnls)), max_dd)
