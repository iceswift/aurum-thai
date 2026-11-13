"""Run one market-aware scrape for an external scheduler."""

import asyncio
import os
import time

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

import main


async def run() -> int:
    market_open, status = main.is_market_open()
    if not market_open:
        print(f"[Worker] Skipping scrape: {status}")
        return 0

    if main.SNAPSHOT_DATABASE_URL:
        await asyncio.to_thread(main.SNAPSHOT_STORE.initialize)
        await asyncio.to_thread(main.restore_snapshot_state)

    main.GLOBAL_CACHE["market_status"] = f"{status} | Shop scraping disabled"
    try:
        await main.start_browser()
        succeeded, _ = await main.update_all_data(scrape_gold=True, scrape_shops=False)
        return 0 if succeeded else 1
    finally:
        await main.stop_browser()


if __name__ == "__main__":
    started = time.monotonic()
    code = 1
    try:
        code = asyncio.run(run())
    finally:
        gateway = os.getenv("METRICS_PUSHGATEWAY")
        if gateway:
            registry = CollectorRegistry()
            Gauge("aurum_worker_duration_seconds", "Duration of the last worker run", registry=registry).set(
                time.monotonic() - started
            )
            Gauge("aurum_worker_exit_code", "Exit code of the last worker run", registry=registry).set(code)
            try:
                import resource

                Gauge("aurum_worker_python_peak_rss_bytes", "Worker Python process peak RSS on Linux", registry=registry).set(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                )
            except ImportError:
                pass
            try:
                push_to_gateway(gateway, job="aurum-scraper", registry=registry, timeout=5)
            except Exception as error:
                print(f"[Worker] Metrics push failed: {type(error).__name__}")
    raise SystemExit(code)
