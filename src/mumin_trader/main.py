from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from config.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_FILE = PROJECT_ROOT / "dashboard" / "index.html"

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
    return {"service": "Mumin AI Autotrader", "status": "foundation", "dashboard": "/dashboard"}


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not DASHBOARD_FILE.is_file():
        raise RuntimeError("Dashboard asset is missing from this deployment.")
    return FileResponse(DASHBOARD_FILE, media_type="text/html")
