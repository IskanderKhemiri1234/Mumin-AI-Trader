from __future__ import annotations

import asyncio
import os
import signal
import sys

import uvicorn


async def main() -> None:
    data_source = os.getenv("MUMIN_MARKET_DATA_SOURCE", "MOCK_SYNTHETIC")
    if data_source != "MOCK_SYNTHETIC":
        raise RuntimeError("Render demo is locked to MOCK_SYNTHETIC market data.")

    database = os.getenv("MUMIN_DB_PATH", "/tmp/mumin_render_demo.sqlite3")
    interval = os.getenv("MUMIN_INTERVAL_SECONDS", "3")
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/run_continuous_paper.py",
        "--db",
        database,
        "--interval-seconds",
        interval,
        "--data-source",
        "MOCK_SYNTHETIC",
    )

    server = uvicorn.Server(
        uvicorn.Config(
            "mumin_trader.main:app",
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        )
    )
    server_task = asyncio.create_task(server.serve())
    worker_task = asyncio.create_task(worker.wait())

    try:
        done, _ = await asyncio.wait(
            {server_task, worker_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_task in done and worker.returncode not in (0, -signal.SIGTERM):
            raise RuntimeError(f"Mock Paper worker exited unexpectedly: {worker.returncode}")
        if server_task in done:
            await server_task
    finally:
        server.should_exit = True
        if worker.returncode is None:
            worker.terminate()
            try:
                await asyncio.wait_for(worker.wait(), timeout=10)
            except asyncio.TimeoutError:
                worker.kill()
                await worker.wait()
        if not server_task.done():
            await server_task


if __name__ == "__main__":
    asyncio.run(main())
