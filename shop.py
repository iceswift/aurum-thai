# shops.py
import asyncio
from playwright.async_api import Page, BrowserContext

TIMEOUT_MS = 60000

# --- Helper function เพื่อลดการเขียนโค้ดซ้ำ ---
async def safe_scrape(func, page, name):
    try:
        print(f"   running {name}...")
        return await func(page)
    except Exception as e:
        print(f"❌ Error {name}: {e}")
        return None
    finally:
        await page.close()

# 1. Aurora
async def scrape_aurora(context: BrowserContext):
    page = await context.new_page()
    url = "https://www.aurora.co.th/price/gold_pricelist/ราคาทองวันนี้"
    await page.goto(url, timeout=TIMEOUT_MS)
    
    # ใช้ wait_for_selector แทน sleep
    try:
        await page.wait_for_selector(".goldden_out h3.g-price", timeout=10000)
        return {
            "shop": "Aurora",
            "bullion_sell": await page.locator(".goldden_out h3.g-price").inner_text(),
            "bullion_buy": await page.locator(".goldden_in h3.g-price").inner_text()
        }
    except: return None

# 2. MTS Gold
async def scrape_mts(context: BrowserContext):
    page = await context.new_page()
    url = "https://www.mtsgold.co.th/mts-price-sm/"
    await page.goto(url, timeout=TIMEOUT_MS)
    
    try:
        await page.wait_for_selector("#buy965mts", timeout=10000)
        return {
            "shop": "MTS Gold",
            "bullion_buy": await page.locator("#buy965mts").inner_text(),
            "bullion_sell": await page.locator("#sell965mts").inner_text(),
            "jewelry_baht": await page.locator("#sell965gold").inner_text() if await page.locator("#sell965gold").is_visible() else None
        }
    except: return None

# 3. Hua Seng Heng
async def scrape_hsh(context: BrowserContext):
    page = await context.new_page()
    url = "https://www.huasengheng.com"
    await page.goto(url, timeout=TIMEOUT_MS)
    
    try:
        await page.wait_for_selector("#bid965", timeout=10000)
        return {
            "shop": "Hua Seng Heng",
            "bullion_buy": await page.locator("#bid965").first.inner_text(),
            "bullion_sell": await page.locator("#ask965").first.inner_text()
        }
    except: return None

# 4. Chin Hua Heng (Shop 5)
async def scrape_chh(context: BrowserContext):
    page = await context.new_page()
    url = "https://chinhuaheng.com/gold"
    await page.goto(url, timeout=TIMEOUT_MS)
    
    try:
        await page.wait_for_selector("#gpb-chh-offer", timeout=10000)
        return {
            "shop": "Chin Hua Heng",
            "bullion_sell": await page.locator("#gpb-chh-offer").inner_text(),
            "bullion_buy": await page.locator("#gpb-chh-bid").inner_text()
        }
    except: return None

# 5. Ausiris (ตัวปัญหา โหลดนาน)
async def scrape_ausiris(context: BrowserContext):
    page = await context.new_page()
    url = "https://www.ausiris.co.th/content/index/goldprice.html"
    await page.goto(url, timeout=TIMEOUT_MS)
    
    # ใช้ asyncio.sleep แทน time.sleep เพื่อไม่ให้บล็อกระบบ
    await asyncio.sleep(10) # ลดเวลาลงเหลือ 10 วิ แล้วเช็คเรื่อยๆ ดีกว่า
    
    try:
        await page.wait_for_selector("#G965B_bid", timeout=20000)
        return {
            "shop": "Ausiris",
            "bullion_buy": await page.locator("#G965B_bid").inner_text(),
            "bullion_sell": await page.locator("#G965B_offer").inner_text()
        }
    except: return None

# --- ฟังก์ชันรวมพลัง ดึงพร้อมกัน 5 ร้าน ---
async def get_all_shops_data(browser):
    context = await browser.new_context(user_agent="Mozilla/5.0...")
    
    # สร้าง Task รอไว้ แต่ยังไม่รัน
    tasks = [
        safe_scrape(scrape_aurora, context, "Aurora"),
        safe_scrape(scrape_mts, context, "MTS"),
        safe_scrape(scrape_hsh, context, "Hua Seng Heng"),
        safe_scrape(scrape_chh, context, "Chin Hua Heng"),
        safe_scrape(scrape_ausiris, context, "Ausiris")
    ]
    
    # สั่งรันพร้อมกัน (Parallel) ประหยัดเวลามาก
    results = await asyncio.gather(*tasks)
    
    await context.close()
    
    # กรองเอาเฉพาะอันที่ไม่ Error (ไม่ None)
    return [r for r in results if r is not None]
