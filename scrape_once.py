"""Run one market-aware scrape for an external scheduler."""

import asyncio

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
    raise SystemExit(asyncio.run(run()))
