from __future__ import annotations

from decimal import Decimal
from enum import Enum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingEnvironment(str, Enum):
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class TradingMode(str, Enum):
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


MAINNET_CONFIRMATION_PHRASE = "I_ACKNOWLEDGE_REAL_MONEY_SPOT_TRADING"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TRADING_ENV: TradingEnvironment = TradingEnvironment.TESTNET
    TRADING_MODE: TradingMode = TradingMode.PAPER
    ALLOW_MAINNET: bool = False
    LIVE_TRADING_ENABLED: bool = False
    TESTNET_ORDER_PREFLIGHT_ENABLED: bool = False
    TESTNET_SPOT_EXECUTION_ENABLED: bool = False
    MAINNET_ORDER_PREFLIGHT_ENABLED: bool = False
    MAINNET_SPOT_EXECUTION_ENABLED: bool = False
    BINANCE_MAINNET_API_KEY: SecretStr = SecretStr("")
    BINANCE_MAINNET_API_SECRET: SecretStr = SecretStr("")
    MAINNET_CONFIRMATION: str = ""
    MAINNET_ORDER_DATABASE_URL: str = "sqlite:///./data/mumin_mainnet_spot.db"
    MAINNET_LEGAL_ELIGIBILITY_CONFIRMED: bool = False
    BINANCE_API_KEY: SecretStr = SecretStr("")
    BINANCE_API_SECRET: SecretStr = SecretStr("")
    BINANCE_RECV_WINDOW_MS: int = Field(default=5000, ge=1000, le=10000)
    BINANCE_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=60)
    INITIAL_CAPITAL_USD: Decimal = Field(default=Decimal("70.00"), gt=0)
    MAX_CAPITAL_ALLOCATION_USD: Decimal = Field(default=Decimal("70.00"), gt=0)
    MAX_TRADE_ALLOCATION_USD: Decimal = Field(default=Decimal("5.00"), gt=0)
    MIN_TRADE_ALLOCATION_USD: Decimal = Field(default=Decimal("5.00"), gt=0)
    CAPITAL_RESERVE_PERCENT: Decimal = Field(default=Decimal("20"), ge=0, lt=100)
    MAX_RISK_PER_TRADE_PERCENT: Decimal = Field(default=Decimal("0.50"), gt=0, le=2)
    MAX_DAILY_LOSS_PERCENT: Decimal = Field(default=Decimal("2.00"), gt=0, le=10)
    MAX_DRAWDOWN_PERCENT: Decimal = Field(default=Decimal("5.00"), gt=0, le=20)
    MAX_OPEN_POSITIONS: int = Field(default=2, ge=1, le=20)
    QUOTE_ASSET: str = "USDT"
    WATCHLIST: str = "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,LINKUSDT"
    ENABLE_BNB_FEE_DISCOUNT: bool = True
    BNB_FEE_ASSET: str = "BNB"
    REQUIRE_BNB_BALANCE_FOR_DISCOUNT: bool = True
    DATABASE_URL: str = "sqlite:///./data/mumin_trader.db"

    @model_validator(mode="after")
    def validate_limits(self) -> "Settings":
        if self.MAX_CAPITAL_ALLOCATION_USD > self.INITIAL_CAPITAL_USD:
            raise ValueError("Capital allocation cannot exceed configured initial capital.")
        if self.MAX_TRADE_ALLOCATION_USD > self.MAX_CAPITAL_ALLOCATION_USD:
            raise ValueError("Trade allocation cannot exceed total capital allocation.")
        if self.MIN_TRADE_ALLOCATION_USD > self.MAX_TRADE_ALLOCATION_USD:
            raise ValueError("Minimum trade allocation exceeds maximum trade allocation.")
        if self.MAX_RISK_PER_TRADE_PERCENT >= self.MAX_DAILY_LOSS_PERCENT:
            raise ValueError("Per-trade risk must be below daily loss limit.")

        if self.TRADING_ENV is TradingEnvironment.TESTNET:
            if self.TRADING_MODE is TradingMode.LIVE or self.LIVE_TRADING_ENABLED or self.ALLOW_MAINNET:
                raise ValueError("Live/Mainnet flags are prohibited in TESTNET configuration.")
            if self.MAINNET_ORDER_PREFLIGHT_ENABLED or self.MAINNET_SPOT_EXECUTION_ENABLED:
                raise ValueError("Mainnet execution flags are prohibited in TESTNET configuration.")
        if self.TESTNET_SPOT_EXECUTION_ENABLED and not self.TESTNET_ORDER_PREFLIGHT_ENABLED:
            raise ValueError("Spot Testnet execution requires the order preflight gate.")
        if self.TESTNET_SPOT_EXECUTION_ENABLED:
            if self.TRADING_ENV is not TradingEnvironment.TESTNET or self.TRADING_MODE is not TradingMode.TESTNET:
                raise ValueError("Spot Testnet execution requires TRADING_ENV=TESTNET and TRADING_MODE=TESTNET.")
            if self.ALLOW_MAINNET or self.LIVE_TRADING_ENABLED:
                raise ValueError("Spot Testnet execution requires all Mainnet/live flags disabled.")
            if not self.BINANCE_API_KEY.get_secret_value() or not self.BINANCE_API_SECRET.get_secret_value():
                raise ValueError("Spot Testnet execution requires dedicated Testnet credentials.")
        if self.TESTNET_ORDER_PREFLIGHT_ENABLED:
            if self.TRADING_ENV is not TradingEnvironment.TESTNET:
                raise ValueError("Testnet order preflight is restricted to TESTNET.")
            if self.TRADING_MODE not in {TradingMode.PAPER, TradingMode.TESTNET}:
                raise ValueError("Testnet preflight requires PAPER or TESTNET mode.")
            if self.ALLOW_MAINNET or self.LIVE_TRADING_ENABLED:
                raise ValueError("Testnet order preflight requires all Mainnet/live flags disabled.")
            if not self.BINANCE_API_KEY.get_secret_value() or not self.BINANCE_API_SECRET.get_secret_value():
                raise ValueError("Testnet order preflight requires Spot Testnet API credentials.")

        if self.TRADING_ENV is TradingEnvironment.MAINNET:
            if self.TRADING_MODE is not TradingMode.LIVE:
                raise ValueError("Mainnet requires TRADING_MODE=LIVE.")
            if not self.ALLOW_MAINNET or not self.LIVE_TRADING_ENABLED:
                raise ValueError("Mainnet requires both independent live-trading gates.")
            if self.MAINNET_CONFIRMATION != MAINNET_CONFIRMATION_PHRASE:
                raise ValueError("Mainnet confirmation phrase is missing or invalid.")
            if not self.MAINNET_LEGAL_ELIGIBILITY_CONFIRMED:
                raise ValueError("Mainnet requires explicit legal-eligibility confirmation.")
            if not self.BINANCE_MAINNET_API_KEY.get_secret_value() or not self.BINANCE_MAINNET_API_SECRET.get_secret_value():
                raise ValueError("Mainnet requires dedicated server-side Spot credentials.")
            if self.TESTNET_ORDER_PREFLIGHT_ENABLED or self.TESTNET_SPOT_EXECUTION_ENABLED:
                raise ValueError("Mainnet and Testnet execution gates cannot be enabled together.")
            if self.MAINNET_SPOT_EXECUTION_ENABLED and not self.MAINNET_ORDER_PREFLIGHT_ENABLED:
                raise ValueError("Mainnet Spot execution requires the Mainnet order preflight gate.")
        elif self.MAINNET_ORDER_PREFLIGHT_ENABLED or self.MAINNET_SPOT_EXECUTION_ENABLED:
            raise ValueError("Mainnet execution gates require TRADING_ENV=MAINNET.")

        if self.TRADING_MODE is TradingMode.LIVE and self.TRADING_ENV is not TradingEnvironment.MAINNET:
            raise ValueError("LIVE mode is restricted to Binance Spot Mainnet.")
        return self

    @property
    def rest_base_url(self) -> str:
        if self.TRADING_ENV is TradingEnvironment.MAINNET:
            return "https://api.binance.com"
        return "https://testnet.binance.vision"

    @property
    def websocket_base_url(self) -> str:
        if self.TRADING_ENV is TradingEnvironment.MAINNET:
            return "wss://stream.binance.com:9443/ws"
        return "wss://stream.testnet.binance.vision/ws"

    @property
    def watchlist_symbols(self) -> list[str]:
        return [item.strip().upper() for item in self.WATCHLIST.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
