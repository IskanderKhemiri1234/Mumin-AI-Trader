from __future__ import annotations

from fastapi import FastAPI

from config.config import get_settings

app = FastAPI(
    title="Mumin AI Autotrader",
    version="0.1.0",
    description="Safety-first quantitative Spot trading infrastructure.",
)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.TRADING_ENV.value,
        "mode": settings.TRADING_MODE.value,
        "execution": "disabled_in_foundation",
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "Mumin AI Autotrader", "status": "foundation"}
