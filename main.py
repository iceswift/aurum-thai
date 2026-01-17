from fastapi import FastAPI, Response
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, Page
import uvicorn
import asyncio
import datetime
from typing import Dict, Any, Optional, List
from shop import scrape_all_shops

# ==============================================================================
# 1. CENTRAL DATA STORE (กองกลางเก็บข้อมูล)
# ==============================================================================
GLOBAL_CACHE: Dict[str, Any] = {
    "gold_bar_data": [],      # เก็บประวัติราคาทองคำแท่ง
    "jewelry_percent": [],    # เก็บราคาทองรูปพรรณ (เฉพาะ %)
    "shop_data": [],          # เก็บข้อมูลจาก 5 ร้านทอง
    "last_updated": None,     # เวลาที่อัปเดตล่าสุด
    "market_status": "Initializing...",
    "source_type": "None"     # เก็บสถานะว่าใช้เว็บไหนอยู่ (New/Classic/None)
}

playwright_instance = None
browser_instance: Optional[Browser] = None

# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================
def get_thai_time():
    """แปลงเวลาปัจจุบันเป็นเวลาไทย (UTC+7)"""
    tz = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(tz)

def is_market_open():
    """
    เช็คเวลาทำการตลาด Gold Traders
    - จันทร์-ศุกร์: 09:00 - 17:45
    - เสาร์: 09:00 - 09:30
    - อาทิตย์: ปิด
    """
    now = get_thai_time()
    weekday = now.weekday() # 0=Mon, 6=Sun
    current = now.time()

    # วันอาทิตย์ (6): ปิดตลอดวัน
    if weekday == 6: 
        return False, "Closed (Sunday)"
    
    # วันเสาร์ (5): เปิดแค่ 09:00 - 09:30
    if weekday == 5:
        if datetime.time(9, 0) <= current <= datetime.time(9, 30):
            return True, "Open (Sat Morning)"
        return False, "Closed (Sat > 09:30)"

    # วันธรรมดา (0-4): เปิด 09:00 - 17:45
    if datetime.time(9, 0) <= current <= datetime.time(17, 45):
        return True, "Open (Weekday)"
        
    return False, "Closed (Outside Hours)"

def is_shop_open():
    """
    เช็คเวลาทำการร้านค้า (24/7 ยกเว้นปิดเสาร์ 9:30 - จันทร์ 00:00)
    - จันทร์ (0) - ศุกร์ (4): เปิด 24 ชม.
    - เสาร์ (5): ปิดหลัง 09:30
    - อาทิตย์ (6): ปิดทั้งวัน
    """
    now = get_thai_time()
    weekday = now.weekday()
    current_time = now.time()

    # วันอาทิตย์ (6): ปิดตลอดวัน
    if weekday == 6:
        return False, "Closed (Sunday)"

    # วันเสาร์ (5): ปิดหลัง 09:30
    if weekday == 5:
        if current_time >= datetime.time(9, 30):
            return False, "Closed (Saturday > 09:30)"

    # วันอื่นๆ (จันทร์-ศุกร์): เปิดตลอด
    return True, "Open (24h)"

# ==============================================================================
# 3. SCRAPING LOGIC (แยกฟังก์ชันตามเวอร์ชันเว็บ)
# ==============================================================================

# --- LOGIC A: เว็บเวอร์ชันใหม่ (Clean URL) ---
async def scrape_new_version(page: Page) -> Dict[str, Any]:
    print("   👉 Trying New Version Logic...")
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

# --- LOGIC B: เว็บเวอร์ชันเก่า (Classic .aspx) ---
async def scrape_classic_version(page: Page) -> Dict[str, Any]:
    print("   👉 Trying Classic Version Logic (Fallback)...")
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
# 4. ORCHESTRATOR & LIFECYCLE MANAGEMENT
# ==============================================================================

async def start_browser():
    global playwright_instance, browser_instance
    if browser_instance: return 

    # print("🚀 [System] Waking up... Starting Browser Engine")
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox', 
            '--disable-setuid-sandbox', 
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-extensions',
            '--no-zygote'
        ]
    )

async def stop_browser():
    global playwright_instance, browser_instance
    if not browser_instance: return 

    print("💤 [System] Hibernate Mode... Shutting down Browser Engine")
    try:
        if browser_instance:
            await browser_instance.close()
        if playwright_instance:
            await playwright_instance.stop()
    except Exception as e:
        print(f"   ⚠️ Shutdown Warning: {e}")
    finally:
        browser_instance = None
        playwright_instance = None

async def update_all_data(scrape_gold: bool = True, scrape_shops: bool = False):
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    
    # ดึงค่า Source ที่จำไว้ (Sticky Session)
    current_source = GLOBAL_CACHE.get("source_type", "None")

    if not browser_instance: 
        print("❌ Error: Browser not running!")
        return

    context = await browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    try:
        page = await context.new_page()
        
        result_data = None
        
        # --- PHASE 1 & 2: Gold Traders (Only if requested) ---
        if scrape_gold:
            # --- PHASE 1: Fast Track ---
            if current_source == "New Website":
                try:
                    result_data = await scrape_new_version(page)
                except Exception:
                    current_source = "None"

            elif current_source == "Classic Website":
                try:
                    result_data = await scrape_classic_version(page)
                except Exception:
                    current_source = "None"

            # --- PHASE 2: Discovery Mode ---
            if current_source == "None" or result_data is None:
                # print(f"🔍 [{now_str}] Discovery Mode: Finding active website...")
                try:
                    result_data = await scrape_new_version(page)
                except Exception:
                    try:
                        result_data = await scrape_classic_version(page)
                    except Exception:
                        print("   ❌ All sources failed.")

            # --- SAVE DATA ---
            if result_data:
                if result_data["gold"]: GLOBAL_CACHE["gold_bar_data"] = result_data["gold"]
                if result_data["jewelry"]: GLOBAL_CACHE["jewelry_percent"] = result_data["jewelry"]
                GLOBAL_CACHE["source_type"] = result_data["source"]
            else:
                GLOBAL_CACHE["source_type"] = "None"

        # --- PHASE 3: Shop Scraping (Parallel) - Only if requested ---
        if scrape_shops:
            print(f"🏭 [{now_str}] Scraping 5 Shops...")
            try:
                shop_results = await scrape_all_shops(context)
                GLOBAL_CACHE["shop_data"] = shop_results
            except Exception as e:
                print(f"   ❌ Shop Scraping Error: {e}")

        GLOBAL_CACHE["last_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")

    except Exception as e:
        print(f"🔥 Critical System Error: {e}")
        GLOBAL_CACHE["source_type"] = "None"
    
    finally:
        # 🛡️ CLEANUP: Always close the context!
        await context.close()

async def run_scheduler():
    tick_counter = 0
    while True:
        is_open, status_msg = is_market_open()
        is_shops_active, shop_status_msg = is_shop_open()
        
        GLOBAL_CACHE["market_status"] = f"{status_msg} | {shop_status_msg}"
        
        # Logic: 
        # 1. Gold Traders: ทำงานเฉพาะตลาดเปิด + ทุก 2 นาที (tick % 2 == 0) -> เพื่อประหยัดค่าใช้จ่าย
        # 2. Shops: ทำงานตลอด (ยกเว้นปิดสุดสัปดาห์) + ทุก 5 นาที (tick % 5 == 0)
        
        do_scrape_gold = is_open and (tick_counter % 2 == 0)
        do_scrape_shops = is_shops_active and (tick_counter % 5 == 0)

        # Optimization: Hibernate (Auto-Wake / Auto-Sleep)
        if do_scrape_gold or do_scrape_shops:
             # Wake Up
             await start_browser()
             await update_all_data(scrape_gold=do_scrape_gold, scrape_shops=do_scrape_shops)
        else:
             # Hibernate
             await stop_browser()
             if tick_counter % 60 == 0:
                print(f"💤 Market Closed ({GLOBAL_CACHE['market_status']}) - RAM Saved!")
        
        tick_counter += 1
        await asyncio.sleep(60)

# ==============================================================================
# 5. LIFESPAN & API ENDPOINTS
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global playwright_instance, browser_instance
    print("🚀 Hybrid System Starting (with Hibernate Mode)...")
    
    # 1. ย้ายการทำงานหนัก (Initial Scrape) ไปไว้ใน Background Task
    # เพื่อให้ FastAPI Start Server เสร็จทันที (ป้องกัน Error 502 / Health Check Timeout)
    async def initial_startup():
        print("⏳ Incoming: Initial Scrape (Background)...")
        await start_browser()
        
        # Force Scrape: บังคับดึงข้อมูล 1 รอบตอนเปิด Server เสมอ (ไม่สนตลาดเปิด/ปิด)
        # เพื่อให้มีข้อมูลใน Cache ไปแสดงผล (จะได้ไม่ขึ้น waiting_for_data)
        await update_all_data(scrape_gold=True, scrape_shops=True)
        
        # เริ่ม Scheduler หลังจาก Initial Scrape เสร็จ
        asyncio.create_task(run_scheduler())

    asyncio.create_task(initial_startup())
    
    yield
    
    print("🛑 System Stopping...")
    await stop_browser()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root(response: Response):
    response.headers["Cache-Control"] = "public, max-age=10, s-maxage=10"
    return {
        "message": "Thai Gold Price API (Hybrid Auto-Switch)",
        "source_used": GLOBAL_CACHE["source_type"],
        "market_status": GLOBAL_CACHE["market_status"],
        "last_updated": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/latest")
def get_latest(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data:
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}
    
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    
    # Logic เลือกข้อมูลล่าสุดตาม Source
    latest_item = {}
    if GLOBAL_CACHE["source_type"] == "Classic Website":
        latest_item = data[0]
    else:
        latest_item = data[-1]

    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "data": latest_item,
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/gold")
def get_gold_buy_only(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data: return {"status": "waiting_for_data"}

    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"

    latest = {}
    if GLOBAL_CACHE["source_type"] == "Classic Website":
        latest = data[0]
    else:
        latest = data[-1]

    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "bullion_buy": latest.get("bullion_buy"),
        "ornament_buy": latest.get("ornament_buy"),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/history")
def get_history(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {
        "count": len(GLOBAL_CACHE["gold_bar_data"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["gold_bar_data"],
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/percent_jewelry")
def get_percent(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {
        "count": len(GLOBAL_CACHE["jewelry_percent"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["jewelry_percent"],
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/shops")
def get_shops(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {
        "count": len(GLOBAL_CACHE["shop_data"]),
        "data": GLOBAL_CACHE["shop_data"],
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)