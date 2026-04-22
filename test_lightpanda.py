import asyncio
from playwright.async_api import async_playwright
import time

async def test_lightpanda():
    print("🚀 Starting Lightpanda Connectivity Test...")
    
    async with async_playwright() as p:
        try:
            # Connect to Lightpanda running in Docker
            # Make sure you've run: docker run -d --name lightpanda -p 9222:9222 lightpanda/browser:nightly
            print("🔗 Connecting to Lightpanda via CDP (ws://127.0.0.1:9222)...")
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            print("✅ Connected successfully!")
            
            context = await browser.new_context()
            page = await context.new_page()
            
            url = "https://www.goldtraders.or.th/updatepricelist"
            print(f"🌐 Navigating to {url}...")
            
            start_time = time.time()
            await page.goto(url, timeout=60000)
            
            # Wait for the table to load
            print("⏳ Waiting for table to load...")
            await page.wait_for_selector("table tbody tr", timeout=30000)
            
            # Extract data
            rows = await page.locator("table tbody tr").all()
            print(f"📊 Found {len(rows)} rows in the price table.")
            
            if len(rows) > 0:
                first_row = rows[0]
                cells = await first_row.locator("td").all()
                if len(cells) >= 5:
                    texts = [await cell.inner_text() for cell in cells]
                    print("\n--- Latest Gold Price Data ---")
                    print(f"Date: {texts[0]}")
                    print(f"Time: {texts[1]}")
                    print(f"Sell (Bullion): {texts[4]}")
                    print(f"Buy (Bullion): {texts[3]}")
                    print("------------------------------\n")
            
            end_time = time.time()
            print(f"⏱️ Scraping completed in {end_time - start_time:.2f} seconds.")
            
            await browser.close()
            
        except Exception as e:
            print(f"❌ Test Failed: {e}")
            print("\n💡 Troubleshooting tips:")
            print("1. Ensure Docker Desktop is running.")
            print("2. Ensure Lightpanda container is running: docker start lightpanda")
            print("3. Check if port 9222 is being blocked.")

if __name__ == "__main__":
    asyncio.run(test_lightpanda())
