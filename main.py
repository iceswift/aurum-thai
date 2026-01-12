from fastapi import FastAPI, Response
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, Page
import uvicorn
import asyncio
import datetime
import shops
from typing import Dict, Any, Optional, List

# สร้าง instance ของ FastAPI
app = FastAPI()

# กำหนด Timeout ทั่วไป 60 วินาที
TIMEOUT_MS = 60000

# ==============================================================================
# 1. CENTRAL DATA STORE (กองกลางเก็บข้อมูล)
# ==============================================================================
GLOBAL_CACHE: Dict[str, Any] = {
    "gold_bar_data": [],      # เก็บประวัติราคาทองคำแท่ง
    "jewelry_percent": [],    # เก็บราคาทองรูปพรรณ (เฉพาะ %)
    "last_updated": None,     # เวลาที่อัปเดตล่าสุด
    "market_status": "Initializing...",
    "source_type": "None",     # เก็บสถานะว่าใช้เว็บไหนอยู่ (New/Classic/None)
    "shops_data": [],       # เก็บข้อมูลร้านค้า
    "shops_updated": None
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
    if datetime.time(9, 0, 0) <= current <= datetime.time(17, 30, 0):
        return True, "Open"
    return False, "Closed (Outside Hours)"

def get_aurora(page):
    url = "https://www.aurora.co.th/price/gold_pricelist/ราคาทองวันนี้"
    data = {"name": "Aurora", "url": url, "error": None}
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        time.sleep(2) # รอโหลดนิดหน่อย
        
        # รอ element
        page.wait_for_selector(".goldden_out h3.g-price", timeout=TIMEOUT_MS)
        
        sell = page.locator(".goldden_out h3.g-price").inner_text().strip()
        buy = page.locator(".goldden_in h3.g-price").inner_text().strip()
        
        data["prices"] = {
            "gold_bar_965": {"buy": buy, "sell": sell}
        }
    except Exception as e:
        data["error"] = str(e)
    return data

def get_mts(page):
    url = "https://www.mtsgold.co.th/mts-price-sm/"
    data = {"name": "MTS Gold", "url": url, "error": None}
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        time.sleep(2)
        page.wait_for_selector("#buy965mts", timeout=TIMEOUT_MS)
        
        data["prices"] = {
            "gold_bar_965": {
                "buy": page.locator("#buy965mts").inner_text().strip(),
                "sell": page.locator("#sell965mts").inner_text().strip()
            },
            "gold_bar_9999": {
                "buy": page.locator("#buy9999mts").inner_text().strip(),
                "sell": page.locator("#sell9999mts").inner_text().strip()
            },
            "ornament_buyback": {}
        }
        
        # เพิ่มราคารับซื้อคืนรูปพรรณ
        if page.locator("#sell965gold").is_visible():
            data["prices"]["ornament_buyback"] = {
                "baht": page.locator("#sell965gold").inner_text().strip(),
                "gram": page.locator("#sell965grm").inner_text().strip()
            }
            
    except Exception as e:
        data["error"] = str(e)
    return data

def get_hua_seng_heng(page):
    url = "https://www.huasengheng.com"
    data = {"name": "Hua Seng Heng", "url": url, "error": None}
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        time.sleep(2)
        page.wait_for_selector("#bid965", timeout=TIMEOUT_MS)
        
        data["prices"] = {
            "gold_bar_965": {
                "buy": page.locator("#bid965").first.inner_text().strip(),
                "sell": page.locator("#ask965").first.inner_text().strip()
            }
        }
        
        if page.locator("#bidjewelry").first.is_visible():
            data["prices"]["gold_ornament_965"] = {
                "buy": page.locator("#bidjewelry").first.inner_text().strip(),
                "sell": page.locator("#askjewelry").first.inner_text().strip()
            }
            
        if page.locator("#bid9999").first.is_visible():
            data["prices"]["gold_bar_9999"] = {
                "buy": page.locator("#bid9999").first.inner_text().strip(),
                "sell": page.locator("#ask9999").first.inner_text().strip()
            }
    except Exception as e:
        data["error"] = str(e)
    return data

def get_ausiris(page):
    # ต้องมี https:// เสมอ
    url = "http://www.ausiris.co.th/content/index/goldprice.html"
    data = {"name": "Ausiris", "url": url, "error": None}
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        
        # --- จุดที่รอ 30 วินาที ตามที่ขอ ---
        print("Ausiris: Waiting 30s...")
        time.sleep(30) 
        
        try:
            page.wait_for_selector("#G965B_bid", timeout=TIMEOUT_MS)
            
            data["prices"] = {
                "gold_bar_965": {
                    "buy": page.locator("#G965B_bid").inner_text().strip(),
                    "sell": page.locator("#G965B_offer").inner_text().strip()
                }
            }
            
            if page.locator("#G9999B_bid").is_visible():
                data["prices"]["gold_bar_9999"] = {
                    "buy": page.locator("#G9999B_bid").inner_text().strip(),
                    "sell": page.locator("#G9999B_offer").inner_text().strip()
                }
        except:
            data["error"] = "Timeout waiting for price table (30s+)"
            
    except Exception as e:
        data["error"] = str(e)
    return data

def get_shop_5(page):
    url = "https://chinhuaheng.com/gold"
    data = {"name": "Chin Hua Heng", "url": url, "error": None}
    try:
        page.goto(url, timeout=TIMEOUT_MS)
        time.sleep(5)
        
        page.wait_for_selector("#gpb-chh-offer", state="visible", timeout=TIMEOUT_MS)
        
        sell = page.locator("#gpb-chh-offer").inner_text().strip()
        buy = page.locator("#gpb-chh-bid").inner_text().strip()
        
        data["prices"] = {
            "gold_bar_965": {"buy": buy, "sell": sell}
        }
        
        if page.locator("#g99Offer").is_visible():
            data["prices"]["gold_bar_9999"] = {
                "sell": page.locator("#g99Offer").inner_text().strip(),
                "buy": page.locator("#g99Bid").inner_text().strip()
            }
            
        if page.locator("#g965Bath").is_visible():
            data["prices"]["gold_ornament_965"] = {
                "sell": page.locator("#g965Bath").inner_text().strip()
            }
            
    except Exception as e:
        data["error"] = str(e)
    return data

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
# 4. ORCHESTRATOR (Sticky Mode)
# ==============================================================================
async def update_all_data():
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    
    # ดึงค่า Source ที่จำไว้ (Sticky Session)
    current_source = GLOBAL_CACHE.get("source_type", "None")

    if not browser_instance: return

    try:
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        result_data = None
        
        # --- PHASE 1: Fast Track (ถ้าจำได้ ใช้ตัวเดิม) ---
        if current_source == "New Website":
            print(f"🔄 [{now_str}] Fast Track: Using New Version...")
            try:
                result_data = await scrape_new_version(page)
            except Exception as e:
                print(f"   ⚠️ Sticky Source Failed: {e}")
                current_source = "None" # Reset to find new source

        elif current_source == "Classic Website":
            print(f"🔄 [{now_str}] Fast Track: Using Classic Version...")
            try:
                result_data = await scrape_classic_version(page)
            except Exception as e:
                print(f"   ⚠️ Sticky Source Failed: {e}")
                current_source = "None" # Reset to find new source

        # --- PHASE 2: Discovery Mode (ถ้าไม่รู้ หรือตัวเดิมพัง) ---
        if current_source == "None" or result_data is None:
            print(f"🔍 [{now_str}] Discovery Mode: Finding active website...")
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
            
            # จำ Source ไว้ใช้รอบหน้า
            GLOBAL_CACHE["source_type"] = result_data["source"]
            GLOBAL_CACHE["last_updated"] = get_thai_time().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ Success! Locked on: {GLOBAL_CACHE['source_type']}")
        else:
            GLOBAL_CACHE["source_type"] = "None"

        await context.close()

    except Exception as e:
        print(f"🔥 Critical System Error: {e}")
        GLOBAL_CACHE["source_type"] = "None"

# *** ฟังก์ชันนี้คือตัวที่หายไป ทำให้เกิด Error ***
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
    
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True, 
        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    )

    # รันครั้งแรกทันที
    await update_all_data()
    
    # รัน Scheduler (ต้องมีฟังก์ชัน run_scheduler ด้านบนก่อนเรียกใช้)
    asyncio.create_task(run_scheduler())
    
    yield
    
    print("🛑 System Stopping...")
    if browser_instance: await browser_instance.close()
    if playwright_instance: await playwright_instance.stop()

app = FastAPI(lifespan=lifespan)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... (Start Browser เดิม) ...
    
    # รัน Worker ของสมาคมฯ (เดิม)
    asyncio.create_task(run_scheduler())
    
    # รัน Worker ของร้านค้า (ใหม่)
    asyncio.create_task(update_shops_worker())
    
    yield
    # ... (Close Browser เดิม) ...

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

@app.get("/scrape")
def scrape_data():
    """
    Endpoint สำหรับสั่งให้ดึงข้อมูลราคาทองคำ
    ระวัง: การเรียก API นี้จะใช้เวลานานกว่า 30-40 วินาที เพราะมีการรอโหลดหน้าเว็บ
    """
    results = []
    
    with sync_playwright() as p:
        # Headless = True (ไม่แสดงหน้าต่าง)
        browser = p.chromium.launch(headless=True)
        
        # ตั้งค่าหน้าจอเหมือน Desktop
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # ดึงข้อมูลทีละร้าน
        results.append(get_aurora(page))
        results.append(get_mts(page))
        results.append(get_hua_seng_heng(page))
        results.append(get_ausiris(page))
        results.append(get_shop_5(page))
        
        browser.close()

    # ส่งค่ากลับเป็น JSON (Dictionary ใน Python)
    # ตรงนี้คือจุดที่เคย Error เรื่องลูกน้ำ ผมจัด Format ให้ถูกต้องแล้ว
    return {
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        "data": results
    }
