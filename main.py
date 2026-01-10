from fastapi import FastAPI, HTTPException, Response
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser
import uvicorn
import asyncio
import datetime
from typing import Dict, Any, Optional

# ==============================================================================
# 1. CENTRAL DATA STORE (กองกลางเก็บข้อมูล)
# ==============================================================================
GLOBAL_CACHE: Dict[str, Any] = {
    "gold_bar_data": [],
    "jewelry_percent": [],
    "jewelry_weight": [],
    "last_updated": None,
    "market_status": "Initializing..."
}

playwright_instance = None
browser_instance: Optional[Browser] = None

# ==============================================================================
# 2. HELPER: TIME & MARKET CHECK
# ==============================================================================
def get_thai_time():
    """แปลงเวลาปัจจุบันเป็นเวลาไทย (UTC+7)"""
    tz = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(tz)

def is_market_open():
    """
    เช็คว่าตอนนี้ตลาดเปิดหรือไม่?
    จันทร์-เสาร์: 09:00:10 - 17:30:10
    """
    now = get_thai_time()
    if now.weekday() == 6: # วันอาทิตย์
        return False, "Closed (Sunday)"
    
    current_time = now.time()
    start_time = datetime.time(9, 0, 10)
    end_time = datetime.time(17, 30, 10)
    
    if start_time <= current_time <= end_time:
        return True, "Open"
    
    return False, "Closed (Outside Hours)"

# ==============================================================================
# 3. BACKGROUND WORKER
# ==============================================================================
async def update_all_data():
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    print(f"🔄 [{now_str}] Scraper Running...")

    if not browser_instance:
        return

    try:
        # ใช้ Browser Context เดิมแต่เปิด Page ใหม่เพื่อความสะอาด
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # --- STEP 1: Gold Bar (UpdatePriceList) ---
        try:
            await page.goto("https://www.goldtraders.or.th/updatepricelist", timeout=30000)
            await page.wait_for_selector("table tbody tr", timeout=10000)
            rows = await page.locator("table tbody tr").all()
            
            temp_gold = []
            for row in rows:
                cells = await row.locator("td").all()
                if len(cells) >= 10:
                    texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
                    temp_gold.append({
                        "date": texts[0].strip(),
                        "time": texts[1].strip(),
                        "bullion_buy": texts[5].strip(),
                        "bullion_sell": texts[6].strip(),
                        "ornament_buy": texts[3].strip(),
                        "ornament_sell": texts[4].strip(),
                        "change": texts[9].replace('\n', '').strip()
                    })
            if temp_gold: GLOBAL_CACHE["gold_bar_data"] = temp_gold
        except Exception as e:
            print(f"❌ Error Step 1: {e}")

        # --- STEP 2: Jewelry (DailyPrices) ---
        try:
            await page.goto("https://www.goldtraders.or.th/dailyprices", timeout=30000)
            await page.wait_for_load_state("domcontentloaded")
            
            # 2.1 Percent
            try:
                await page.wait_for_selector("td:has-text('96.5%')", timeout=5000)
                rows = await page.locator("table").filter(has_text="96.5%").locator("tbody tr").all()
                temp_percent = []
                for row in rows:
                    cells = await row.locator("td").all()
                    if len(cells) >= 4:
                        texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
                        temp_percent.append({
                            "type": texts[0].strip(),
                            "buy": texts[2].strip(),
                            "sell": texts[3].strip()
                        })
                if temp_percent: GLOBAL_CACHE["jewelry_percent"] = temp_percent
            except: pass

            # 2.2 Weight
            try:
                await page.wait_for_selector("th:has-text('น้ำหนักทอง')", timeout=5000)
                rows = await page.locator("table").filter(has_text="น้ำหนักทอง").locator("tbody tr").all()
                temp_weight = []
                for row in rows:
                    cells = await row.locator("td").all()
                    if len(cells) >= 3:
                        texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
                        temp_weight.append({
                            "weight": texts[0].strip(),
                            "price": texts[1].strip(),
                            "total": texts[2].strip()
                        })
                if temp_weight: GLOBAL_CACHE["jewelry_weight"] = temp_weight
            except: pass
            
        except Exception as e:
            print(f"❌ Error Step 2: {e}")

        GLOBAL_CACHE["last_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")
        print(f"✅ Update Success at {GLOBAL_CACHE['last_updated']}")
        
        await context.close()

    except Exception as e:
        print(f"🔥 Critical Scraper Error: {e}")

async def run_scheduler():
    while True:
        is_open, status_msg = is_market_open()
        GLOBAL_CACHE["market_status"] = status_msg
        
        if is_open:
            await update_all_data()
        else:
            print(f"💤 Market Closed ({status_msg}). Using cached data.")
        
        await asyncio.sleep(60)

# ==============================================================================
# 4. LIFESPAN MANAGER
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

    print("⚡ Initial Boot: Fetching data once...")
    await update_all_data() # ดึงข้อมูลครั้งแรกทันที ไม่ต้องรอรอบ
    
    asyncio.create_task(run_scheduler()) # ปล่อย Worker ทำงานเบื้องหลัง
    
    yield
    
    print("🛑 System Stopping...")
    if browser_instance: await browser_instance.close()
    if playwright_instance: await playwright_instance.stop()

app = FastAPI(lifespan=lifespan)

# ==============================================================================
# 5. API ENDPOINTS (With Cloudflare Caching)
# ==============================================================================

@app.get("/")
def read_root(response: Response):
    # หน้าแรก Cache สั้นๆ 10 วินาทีพอ เผื่อไว้ดู Status
    response.headers["Cache-Control"] = "public, max-age=10, s-maxage=10"
    return {
        "message": "Thai Gold Price API (Cloudflare Ready)",
        "market_status": GLOBAL_CACHE["market_status"],
        "last_updated": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/latest")
def get_latest(response: Response):
    """
    Endpoint นี้สำคัญที่สุด!
    เราใส่ Header บอก Cloudflare ว่า "จำคำตอบนี้ไว้ 60 วินาทีนะ"
    """
    data = GLOBAL_CACHE["gold_bar_data"]
    
    if not data:
        # ถ้าไม่มีข้อมูล (Server เพิ่งตื่นและยังดึงไม่เสร็จ) ไม่ต้อง Cache นาน
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}

    # --- CLOUDFLARE MAGIC HEADER ---
    # public: Cache ได้ทุกคน
    # max-age=60: มือถือ User จำไว้ 60 วิ
    # s-maxage=60: Cloudflare CDN จำไว้ 60 วิ (สำคัญมาก!)
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    
    return {
        "status": "success", 
        "market_status": GLOBAL_CACHE["market_status"],
        "data": data[-1], 
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/gold")
def get_gold_buy_only(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data: return {"status": "waiting_for_data"}

    # Cloudflare Cache
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"

    latest = data[-1]
    return {
        "status": "success",
        "bullion_buy": latest.get("bullion_buy"),
        "ornament_buy": latest.get("ornament_buy"),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/history")
def get_history(response: Response):
    # Cloudflare Cache
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {"count": len(GLOBAL_CACHE["gold_bar_data"]), "data": GLOBAL_CACHE["gold_bar_data"]}

@app.get("/api/percent_jewelry")
def get_percent(response: Response):
    # Cloudflare Cache
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {"count": len(GLOBAL_CACHE["jewelry_percent"]), "data": GLOBAL_CACHE["jewelry_percent"]}

@app.get("/api/weight_jewelry")
def get_weight(response: Response):
    # Cloudflare Cache
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    return {"count": len(GLOBAL_CACHE["jewelry_weight"]), "data": GLOBAL_CACHE["jewelry_weight"]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)