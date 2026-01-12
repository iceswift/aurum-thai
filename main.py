from fastapi import FastAPI, Response
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, Page
import uvicorn
import asyncio
import datetime
from typing import Dict, Any, Optional, List

# ==============================================================================
# 1. CENTRAL DATA STORE (กองกลางเก็บข้อมูล)
# ==============================================================================
GLOBAL_CACHE: Dict[str, Any] = {
    "gold_bar_data": [],      # เก็บประวัติราคาทองคำแท่ง
    "jewelry_percent": [],    # เก็บราคาทองรูปพรรณ (เฉพาะ %)
    "last_updated": None,     # เวลาที่อัปเดตล่าสุด
    "market_status": "Initializing...",
    "source_type": "None"     # ระบุว่ารอบนี้ดึงจาก 'New Website' หรือ 'Classic Website'
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
    """เช็คเวลาทำการตลาด (จันทร์-เสาร์ 09:00 - 17:30)"""
    now = get_thai_time()
    if now.weekday() == 6: return False, "Closed (Sunday)"
    
    current = now.time()
    # เปิด 09:00 - 17:30
    if datetime.time(9, 0, 0) <= current <= datetime.time(17, 30, 0):
        return True, "Open"
    return False, "Closed (Outside Hours)"

# ==============================================================================
# 3. SCRAPING LOGIC (แยกฟังก์ชันตามเวอร์ชันเว็บ)
# ==============================================================================

# --- LOGIC A: เว็บเวอร์ชันใหม่ (Clean URL) ---
async def scrape_new_version(page: Page) -> Dict[str, Any]:
    print("   👉 Trying New Version Logic...")
    # URL เว็บใหม่
    await page.goto("https://www.goldtraders.or.th/updatepricelist", timeout=15000)
    
    # เช็คว่าใช่เว็บใหม่จริงไหม (Table โครงสร้างใหม่)
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

    # 2. Jewelry Percent (ไปหน้า DailyPrices ของเว็บใหม่)
    jewelry_data = []
    try:
        await page.goto("https://www.goldtraders.or.th/dailyprices", timeout=15000)
        # สังเกต element ของเว็บใหม่
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
    # URL เว็บเก่า
    await page.goto("https://www.goldtraders.or.th/UpdatePriceList.aspx", timeout=15000)
    
    # เช็ค Selector ของ GridView (เอกลักษณ์เว็บเก่า)
    await page.wait_for_selector("#DetailPlace_MainGridView", timeout=5000)

    # 1. Gold Bar
    gold_data = []
    rows = await page.locator("#DetailPlace_MainGridView tr:has(td)").all()
    for row in rows:
        cells = await row.locator("td").all()
        # Classic GridView มี 9 ช่อง (วันที่กับเวลารวมกัน)
        if len(cells) >= 9:
            texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
            
            # แยก Date/Time
            raw_dt = texts[0].strip().split()
            d_part = raw_dt[0] if len(raw_dt) > 0 else ""
            t_part = raw_dt[1] if len(raw_dt) > 1 else ""
            
            gold_data.append({
                "date": d_part,
                "time": t_part,
                "round": texts[1].strip(),
                "bullion_buy": texts[2].strip(),   # สังเกต index ต่างจากเว็บใหม่
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
# 4. ORCHESTRATOR (ผู้ควบคุมการทำงาน)
# ==============================================================================
async def update_all_data():
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    print(f"\n🔄 [{now_str}] Scraper Started...")

    if not browser_instance: return

    try:
        # เปิด Page ใหม่ทุกรอบเพื่อความสะอาด (ป้องกัน Cache ค้างใน Browser)
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        result_data = None
        
        # --- PLAN A: ลองเว็บใหม่ก่อน ---
        try:
            result_data = await scrape_new_version(page)
        except Exception as e:
            print(f"   ❌ New Version Failed: {e}")
            print("   🔀 Switching to Fallback (Classic)...")
            
            # --- PLAN B: ถ้าเว็บใหม่พัง ให้ลองเว็บเก่า ---
            try:
                result_data = await scrape_classic_version(page)
            except Exception as e2:
                print(f"   ❌ Classic Version Also Failed: {e2}")

        # --- UPDATE GLOBAL CACHE ---
        if result_data:
            # อัปเดตเฉพาะที่มีข้อมูล
            if result_data["gold"]: 
                GLOBAL_CACHE["gold_bar_data"] = result_data["gold"]
            if result_data["jewelry"]: 
                GLOBAL_CACHE["jewelry_percent"] = result_data["jewelry"]
            
            GLOBAL_CACHE["source_type"] = result_data["source"]
            GLOBAL_CACHE["last_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ Success! Data updated from: {GLOBAL_CACHE['source_type']}")
        else:
            print("🔥 All methods failed. Keeping old cached data.")

        await context.close()

    except Exception as e:
        print(f"🔥 Critical System Error: {e}")

async def run_scheduler():
    while True:
        is_open, status_msg = is_market_open()
        GLOBAL_CACHE["market_status"] = status_msg
        
        if is_open:
            await update_all_data()
        else:
            print(f"💤 Market Closed ({status_msg})")
        
        await asyncio.sleep(60)

# ==============================================================================
# 5. LIFESPAN & API ENDPOINTS
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global playwright_instance, browser_instance
    print("🚀 Hybrid System Starting...")
    
    # เริ่ม Playwright
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True, 
        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    )

    # ดึงข้อมูลรอบแรกทันทีไม่ต้องรอ
    await update_all_data()
    
    # รัน Scheduler เบื้องหลัง
    asyncio.create_task(run_scheduler())
    
    yield
    
    # ปิดระบบ
    print("🛑 System Stopping...")
    if browser_instance: await browser_instance.close()
    if playwright_instance: await playwright_instance.stop()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root(response: Response):
    # Cache สั้นๆ
    response.headers["Cache-Control"] = "public, max-age=10, s-maxage=10"
    return {
        "message": "Thai Gold Price API (Hybrid Auto-Switch)",
        "source_used": GLOBAL_CACHE["source_type"],
        "market_status": GLOBAL_CACHE["market_status"],
        "last_updated": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/latest")
def get_latest(response: Response):
    """
    ดึงข้อมูลล่าสุดโดยอัตโนมัติ
    - ถ้ามาจาก Classic: ล่าสุดคือ Index [0]
    - ถ้ามาจาก New: ล่าสุดคือ Index [-1] (ตัวสุดท้าย)
    """
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data:
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}
    
    # บอก Cloudflare ให้ Cache 60 วิ
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    
    # Logic เลือกข้อมูลล่าสุดตาม Source
    latest_item = {}
    if GLOBAL_CACHE["source_type"] == "Classic Website":
        latest_item = data[0]
    else:
        # Default for New Website (Usually appends to bottom)
        latest_item = data[-1]

    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "data": latest_item,
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/gold")
def get_gold_buy_only(response: Response):
    """ดึงเฉพาะราคารับซื้อ (Buy) เพื่อนำไปใช้ง่ายๆ"""
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data: return {"status": "waiting_for_data"}

    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"

    # หาตัวล่าสุดด้วย Logic เดิม
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
    """ดึงประวัติราคาทั้งหมดที่มีในตาราง"""
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {
        "count": len(GLOBAL_CACHE["gold_bar_data"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["gold_bar_data"],
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/percent_jewelry")
def get_percent(response: Response):
    """ดึงราคาทองรูปพรรณ (Jewelry)"""
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {
        "count": len(GLOBAL_CACHE["jewelry_percent"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["jewelry_percent"],
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)