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
    LIVE = "LIVE"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TRADING_ENV: TradingEnvironment = TradingEnvironment.TESTNET
    TRADING_MODE: TradingMode = TradingMode.PAPER
    ALLOW_MAINNET: bool = False
    LIVE_TRADING_ENABLED: bool = False
    MAINNET_CONFIRMATION: str = ""
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
    WATCHLIST: str = "BTCUSDT,ETHUSDT,BNBUSDT"
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
        if self.TRADING_ENV == TradingEnvironment.TESTNET and (self.TRADING_MODE == TradingMode.LIVE or self.LIVE_TRADING_ENABLED):
            raise ValueError("Live trading is prohibited in TESTNET configuration.")
        if self.TRADING_ENV == TradingEnvironment.MAINNET:
            required = (self.ALLOW_MAINNET, self.LIVE_TRADING_ENABLED, self.TRADING_MODE == TradingMode.LIVE, self.MAINNET_CONFIRMATION == "I_UNDERSTAND_LIVE_RISK", self.MAINNET_LEGAL_ELIGIBILITY_CONFIRMED)
            if not all(required):
                raise ValueError("MAINNET blocked: explicit safety and eligibility checks failed.")
            if not self.BINANCE_API_KEY.get_secret_value() or not self.BINANCE_API_SECRET.get_secret_value():
                raise ValueError("MAINNET requires API credentials.")
        return self

    @property
    def rest_base_url(self) -> str:
        return "https://testnet.binance.vision" if self.TRADING_ENV == TradingEnvironment.TESTNET else "https://api.binance.com"

    @property
    def websocket_base_url(self) -> str:
        return "wss://stream.testnet.binance.vision/ws" if self.TRADING_ENV == TradingEnvironment.TESTNET else "wss://stream.binance.com:9443/ws"

    @property
    def watchlist_symbols(self) -> list[str]:
        return [item.strip().upper() for item in self.WATCHLIST.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
