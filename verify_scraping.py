import asyncio
from playwright.async_api import async_playwright
from shop import scrape_all_shops

async def test_scraping():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        print("Running test scraping...")
        results = await scrape_all_shops(context)
        print("\nResults:")
        import json
        print(json.dumps(results, indent=2, ensure_ascii=True))
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_scraping())
