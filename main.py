import asyncio
import datetime
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List

import uvicorn
from fastapi import FastAPI, Response
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ==============================================================================
# 1. CONFIG & CENTRAL DATA STORE (ตั้งค่าและตัวแปรเก็บข้อมูล)
# ==============================================================================
TIMEOUT_MS = 60000  # Timeout 60 วินาที

GLOBAL_CACHE: Dict[str, Any] = {
    # --- ข้อมูลสมาคมค้าทองคำ ---
    "gold_bar_data": [],      
    "jewelry_percent": [],    
    "association_updated": None,     
    "market_status": "Initializing...",
    "source_type": "None",     # New Website / Classic Website
    
    # --- ข้อมูลร้านทองรายย่อย ---
    "shops_data": [],
    "shops_updated": None
}

playwright_instance = None
browser_instance: Optional[Browser] = None

# ==============================================================================
# 2. HELPER FUNCTIONS (ฟังก์ชันช่วยคำนวณเวลา)
# ==============================================================================
def get_thai_time():
    """แปลงเวลาปัจจุบันเป็นเวลาไทย (UTC+7)"""
    tz = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(tz)

def is_market_open():
    """เช็คเวลาทำการตลาด (จันทร์-เสาร์ 09:00 - 17:30)"""
    now = get_thai_time()
    if now.weekday() == 6: return False, "Closed (Sunday)"
    
    current = now.time()
    if datetime.time(9, 0, 0) <= current <= datetime.time(17, 30, 0):
        return True, "Open"
    return False, "Closed (Outside Hours)"

# ==============================================================================
# 3. PART A: GOLD ASSOCIATION SCRAPERS (สมาคมค้าทองคำ)
# ==============================================================================

async def scrape_new_version(page: Page) -> Dict[str, Any]:
    print("   👉 [Assoc] Trying New Version Logic...")
    await page.goto("https://www.goldtraders.or.th/updatepricelist", timeout=15000)
    await page.wait_for_selector("table tbody tr", timeout=5000) 

    # 1. Gold Bar
    gold_data = []
    rows = await page.locator("table tbody tr").all()
    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) >= 10:
            texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
            gold_data.append({
                "date": texts[0].strip(),
                "time": texts[1].strip(),
                "round": texts[2].strip(),
                "ornament_buy": texts[3].strip(),
                "ornament_sell": texts[4].strip(),
                "bullion_buy": texts[5].strip(),
                "bullion_sell": texts[6].strip(),
                "gold_spot": texts[7].strip(),
                "thb": texts[8].strip(),
                "change": texts[9].replace('\n', '').strip()
            })

    # 2. Jewelry Percent
    jewelry_data = []
    try:
        await page.goto("https://www.goldtraders.or.th/dailyprices", timeout=15000)
        await page.wait_for_selector("td:has-text('96.5%')", timeout=5000)
        rows = await page.locator("table").filter(has_text="96.5%").locator("tbody tr").all()
        for row in rows:
            cells = await row.locator("td").all()
            if len(cells) >= 4:
                texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
                jewelry_data.append({
                    "type": texts[0].strip(),
                    "buy": texts[2].strip(),
                    "sell": texts[3].strip()
                })
    except Exception as e:
        print(f"   ⚠️ New Version Jewelry Error: {e}")

    return {"gold": gold_data, "jewelry": jewelry_data, "source": "New Website"}

async def scrape_classic_version(page: Page) -> Dict[str, Any]:
    print("   👉 [Assoc] Trying Classic Version Logic (Fallback)...")
    await page.goto("https://www.goldtraders.or.th/UpdatePriceList.aspx", timeout=15000)
    await page.wait_for_selector("#DetailPlace_MainGridView", timeout=5000)

    # 1. Gold Bar
    gold_data = []
    rows = await page.locator("#DetailPlace_MainGridView tr:has(td)").all()
    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) >= 9:
            texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
            raw_dt = texts[0].strip().split()
            d_part = raw_dt[0] if len(raw_dt) > 0 else ""
            t_part = raw_dt[1] if len(raw_dt) > 1 else ""
            
            gold_data.append({
                "date": d_part,
                "time": t_part,
                "round": texts[1].strip(),
                "bullion_buy": texts[2].strip(),
                "bullion_sell": texts[3].strip(),
                "ornament_buy": texts[4].strip(),
                "ornament_sell": texts[5].strip(),
                "gold_spot": texts[6].strip(),
                "thb": texts[7].strip(),
                "change": texts[8].strip()
            })

    # 2. Jewelry Percent
    jewelry_data = []
    try:
        await page.goto("https://www.goldtraders.or.th/DailyPrices.aspx", timeout=15000)
        await page.wait_for_selector("#DetailPlace_MainGridView", timeout=5000)
        rows = await page.locator("#DetailPlace_MainGridView tr:has(td)").all()
        for row in rows:
            cells = await row.locator("td").all()
            if len(cells) >= 4:
                texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
                jewelry_data.append({
                    "type": texts[0].strip(),
                    "buy": texts[2].strip(),
                    "sell": texts[3].strip()
                })
    except Exception as e:
        print(f"   ⚠️ Classic Version Jewelry Error: {e}")

    return {"gold": gold_data, "jewelry": jewelry_data, "source": "Classic Website"}

# ==============================================================================
# 4. PART B: PRIVATE SHOPS SCRAPERS (ร้านทองเอกชน)
# ==============================================================================

async def safe_scrape(func, page, name):
    """ตัวช่วยรัน Scraper ร้านค้า ถ้า Error จะไม่ทำให้ระบบล่ม"""
    try:
        # print(f"   running {name}...")
        return await func(page)
    except Exception as e:
        print(f"❌ Error {name}: {e}")
        return None
    finally:
        await page.close()

# --- Shop 1: Aurora ---
async def scrape_aurora(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://www.aurora.co.th/price/gold_pricelist/ราคาทองวันนี้", timeout=TIMEOUT_MS)
        await page.wait_for_selector(".goldden_out h3.g-price", timeout=20000)
        return {
            "shop": "Aurora",
            "bullion_sell": await page.locator(".goldden_out h3.g-price").inner_text(),
            "bullion_buy": await page.locator(".goldden_in h3.g-price").inner_text()
        }
    except: return None

# --- Shop 2: MTS Gold ---
async def scrape_mts(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://www.mtsgold.co.th/mts-price-sm/", timeout=TIMEOUT_MS)
        await page.wait_for_selector("#buy965mts", timeout=20000)
        return {
            "shop": "MTS Gold",
            "bullion_buy": await page.locator("#buy965mts").inner_text(),
            "bullion_sell": await page.locator("#sell965mts").inner_text()
        }
    except: return None

# --- Shop 3: Hua Seng Heng ---
async def scrape_hsh(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://www.huasengheng.com", timeout=TIMEOUT_MS)
        await page.wait_for_selector("#bid965", timeout=20000)
        return {
            "shop": "Hua Seng Heng",
            "bullion_buy": await page.locator("#bid965").first.inner_text(),
            "bullion_sell": await page.locator("#ask965").first.inner_text()
        }
    except: return None

# --- Shop 4: Chin Hua Heng ---
async def scrape_chh(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://chinhuaheng.com/gold", timeout=TIMEOUT_MS)
        await page.wait_for_selector("#gpb-chh-offer", timeout=20000)
        return {
            "shop": "Chin Hua Heng",
            "bullion_sell": await page.locator("#gpb-chh-offer").inner_text(),
            "bullion_buy": await page.locator("#gpb-chh-bid").inner_text()
        }
    except: return None

# --- Shop 5: Ausiris ---
async def scrape_ausiris(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://www.ausiris.co.th/content/index/goldprice.html", timeout=TIMEOUT_MS)
        await asyncio.sleep(5) # รอโหลด JS นิดหน่อย
        try:
            await page.wait_for_selector("#G965B_bid", timeout=30000) # รอสูงสุด 30 วิ
            return {
                "shop": "Ausiris",
                "bullion_buy": await page.locator("#G965B_bid").inner_text(),
                "bullion_sell": await page.locator("#G965B_offer").inner_text()
            }
        except: return None
    except: return None

# ==============================================================================
# 5. WORKERS & SCHEDULERS (ตัวทำงานเบื้องหลัง)
# ==============================================================================

# Worker 1: อัปเดตราคาสมาคม (Association)
async def update_association_data():
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    current_source = GLOBAL_CACHE.get("source_type", "None")
    
    if not browser_instance: return

    try:
        context = await browser_instance.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        result_data = None
        
        # --- Sticky Logic ---
        if current_source == "New Website":
            try: result_data = await scrape_new_version(page)
            except: current_source = "None"
        elif current_source == "Classic Website":
            try: result_data = await scrape_classic_version(page)
            except: current_source = "None"

        # --- Discovery Mode ---
        if current_source == "None" or result_data is None:
            try: result_data = await scrape_new_version(page)
            except:
                try: result_data = await scrape_classic_version(page)
                except: print("   ❌ [Assoc] All sources failed.")

        # --- Update Cache ---
        if result_data:
            if result_data["gold"]: GLOBAL_CACHE["gold_bar_data"] = result_data["gold"]
            if result_data["jewelry"]: GLOBAL_CACHE["jewelry_percent"] = result_data["jewelry"]
            GLOBAL_CACHE["source_type"] = result_data["source"]
            GLOBAL_CACHE["association_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ [Assoc] Success! Source: {GLOBAL_CACHE['source_type']}")
        else:
            GLOBAL_CACHE["source_type"] = "None"
        
        await context.close()
    except Exception as e:
        print(f"🔥 [Assoc] Error: {e}")

# Worker 2: อัปเดตร้านค้า (Shops)
async def update_shops_data():
    global GLOBAL_CACHE
    if not browser_instance: return
    
    print("\n🏪 [Shops] Updating 5 shops data...")
    try:
        context = await browser_instance.new_context(user_agent="Mozilla/5.0...")
        
        # สร้าง Tasks ดึงข้อมูลพร้อมกัน
        tasks = [
            safe_scrape(scrape_aurora, context, "Aurora"),
            safe_scrape(scrape_mts, context, "MTS"),
            safe_scrape(scrape_hsh, context, "Hua Seng Heng"),
            safe_scrape(scrape_chh, context, "Chin Hua Heng"),
            safe_scrape(scrape_ausiris, context, "Ausiris")
        ]
        
        results = await asyncio.gather(*tasks)
        await context.close()
        
        # กรองเอาเฉพาะร้านที่ดึงสำเร็จ
        clean_results = [r for r in results if r is not None]
        
        if clean_results:
            GLOBAL_CACHE["shops_data"] = clean_results
            GLOBAL_CACHE["shops_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ [Shops] Updated successfully ({len(clean_results)} shops).")
        else:
            print("⚠️ [Shops] No data retrieved.")
            
    except Exception as e:
        print(f"🔥 [Shops] Error: {e}")

# Scheduler Loops
async def run_association_scheduler():
    """Loop เช็คราคาสมาคม ทุก 60 วินาที"""
    while True:
        is_open, status_msg = is_market_open()
        GLOBAL_CACHE["market_status"] = status_msg
        if is_open:
            await update_association_data()
        else:
            print(f"💤 [Assoc] Market Closed ({status_msg})")
        await asyncio.sleep(60)

async def run_shops_scheduler():
    """Loop เช็คราคาร้านค้า ทุก 5 นาที (300 วินาที)"""
    while True:
        # ร้านค้าเช็คตลอดก็ได้ หรือจะเช็คเฉพาะตลาดเปิดก็ได้ (ที่นี้ตั้งให้ทำตลอด)
        await update_shops_data()
        await asyncio.sleep(300)

# ==============================================================================
# 6. LIFESPAN & API ENDPOINTS
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global playwright_instance, browser_instance
    print("🚀 System Starting...")
    
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True, 
        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    )

    # Initial Fetch (ดึงข้อมูลครั้งแรกตอนเริ่มเซิร์ฟเวอร์)
    print("⚡ Initial Boot: Fetching all data...")
    await update_association_data()
    await update_shops_data()
    
    # Start Background Loops
    asyncio.create_task(run_association_scheduler())
    asyncio.create_task(run_shops_scheduler())
    
    yield
    
    print("🛑 System Stopping...")
    if browser_instance: await browser_instance.close()
    if playwright_instance: await playwright_instance.stop()

app = FastAPI(lifespan=lifespan)

# --- Endpoints ---

@app.get("/")
def read_root(response: Response):
    response.headers["Cache-Control"] = "public, max-age=10"
    return {
        "message": "Thai Gold & Shops API (Unified)",
        "market_status": GLOBAL_CACHE["market_status"],
        "association_updated": GLOBAL_CACHE["association_updated"],
        "shops_updated": GLOBAL_CACHE["shops_updated"]
    }

@app.get("/api/latest")
def get_latest(response: Response):
    """ราคาสมาคมล่าสุด"""
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data: return {"status": "waiting"}
    
    response.headers["Cache-Control"] = "public, max-age=60"
    
    latest_item = data[0] if GLOBAL_CACHE["source_type"] == "Classic Website" else data[-1]
    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "data": latest_item,
        "updated_at": GLOBAL_CACHE["association_updated"]
    }

@app.get("/api/shops")
def get_shops(response: Response):
    """ราคาร้านทองเอกชน"""
    response.headers["Cache-Control"] = "public, max-age=300"
    return {
        "status": "success",
        "count": len(GLOBAL_CACHE["shops_data"]),
        "updated_at": GLOBAL_CACHE["shops_updated"],
        "data": GLOBAL_CACHE["shops_data"]
    }

@app.get("/api/history")
def get_history(response: Response):
    """ประวัติราคาสมาคม"""
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "count": len(GLOBAL_CACHE["gold_bar_data"]),
        "data": GLOBAL_CACHE["gold_bar_data"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
