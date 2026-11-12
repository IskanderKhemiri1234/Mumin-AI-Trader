from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CandidateSignal:
    symbol: str
    direction: str
    confidence: Decimal
    rationale: str


class DecisionEngine:
    """AI output is advisory and never has execution authority."""

    def validate(self, signal: CandidateSignal, minimum_confidence: Decimal) -> bool:
        return (
            signal.direction.upper() in {"BUY", "SELL", "HOLD"}
            and Decimal("0") <= signal.confidence <= Decimal("1")
            and signal.confidence >= minimum_confidence
            and bool(signal.symbol.strip())
        )
