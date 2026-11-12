from decimal import Decimal
from typing import Any

from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient


class CommissionService:
    def __init__(self, client: BinanceSpotRESTClient):
        self.client = client

    async def get_symbol_commission(self, symbol: str) -> dict[str, Any]:
        payload = await self.client.account_commission(symbol)
        discount = payload.get("discount", {})
        return {
            "symbol": symbol.upper(),
            "maker_rate": Decimal(str(payload["standardCommission"]["maker"])),
            "taker_rate": Decimal(str(payload["standardCommission"]["taker"])),
            "bnb_discount_enabled_for_account": bool(discount.get("enabledForAccount")),
            "bnb_discount_enabled_for_symbol": bool(discount.get("enabledForSymbol")),
            "discount_asset": discount.get("discountAsset"),
            "discount_rate": Decimal(str(discount.get("discount", "0"))),
        }
