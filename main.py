from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, Page
import uvicorn
import asyncio
import datetime
from typing import Dict, Any, Optional, List
import os
import json
import firebase_admin
from firebase_admin import credentials, messaging
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
# 2. FIREBASE & NOTIFICATION CONFIG (กำหนดค่า Firebase และการแจ้งเตือน)
# ==============================================================================
# Cache ราคาสุดท้ายเพื่อป้องกันการส่งข้อความซ้ำ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.getenv("NOTIFICATION_STATE_FILE", os.path.join(BASE_DIR, "notification_state.json"))
CRED_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", os.path.join(BASE_DIR, "firebase-service-account.json"))
STALE_AFTER_MINUTES = int(os.getenv("STALE_AFTER_MINUTES", "10"))

def load_notification_state():
    """โหลดสถานะการแจ้งเตือนจากไฟล์ JSON"""
    default_state = {
        "last_gold_bar_sell": None,
        "last_update_time": None,
        "last_sent_at": None
    }
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                print(f"✅ [NotifState] Loaded state from {STATE_FILE}")
                return state
    except Exception as e:
        print(f"⚠️ [NotifState] Load failed: {e}")
    return default_state

def save_notification_state(state):
    """บันทึกสถานะการแจ้งเตือนลงไฟล์ JSON"""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [NotifState] Save failed: {e}")

# โหลดสถานะเริ่มต้น
current_state = load_notification_state()

NOTIF_CACHE = {
    "last_gold_bar_sell": current_state.get("last_gold_bar_sell"),
    "last_update_time": current_state.get("last_update_time"),
    "last_sent_at": current_state.get("last_sent_at"),
    "topic_name": "gold_price_updates"
}

# เริ่มต้น Firebase Admin SDK
try:
    if os.path.exists(CRED_PATH):
        cred = credentials.Certificate(CRED_PATH)
        firebase_admin.initialize_app(cred)
        print(f"✅ [Firebase] SDK Initialized Successfully (Using: {os.path.basename(CRED_PATH)})")
    else:
        print(f"⚠️ [Firebase] Warning: Credentials not found at {CRED_PATH}. Push notifications disabled.")
except Exception as e:
    print(f"❌ [Firebase] Initialization Error: {e}")

async def send_push_notification(title: str, body: str, data: Dict[str, str] = None):
    """ส่ง Push Notification ผ่าน FCM Topic"""
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            topic=NOTIF_CACHE["topic_name"],
        )
        response = messaging.send(message)
        print(f"🔔 [Push] Sent Success: {response}")
        
        # อัปเดตสถานะการส่งสำเร็จหลังจากส่งจริงเท่านั้น
        NOTIF_CACHE["last_sent_at"] = get_thai_time().isoformat()
        save_notification_state({
            "last_gold_bar_sell": NOTIF_CACHE["last_gold_bar_sell"],
            "last_update_time": NOTIF_CACHE["last_update_time"],
            "last_sent_at": NOTIF_CACHE["last_sent_at"]
        })
    except Exception as e:
        print(f"❌ [Push] Send Error: {e}")

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================
def set_public_cache(response: Response, max_age=60, s_maxage=60):
    """กำหนด Cache-Control header สำหรับ Public API"""
    # stale-while-revalidate ช่วยให้ user ได้ข้อมูลเร็วขึ้นขณะที่ server อัปเดตข้อมูลเบื้องหลัง
    swr = 60 if max_age >= 60 else 30
    response.headers["Cache-Control"] = f"public, max-age={max_age}, s-maxage={s_maxage}, stale-while-revalidate={swr}"

def set_no_store(response: Response):
    """กำหนดไม่ให้ Cache ข้อมูล (สำหรับข้อมูลสถานะหรือข้อมูลที่ยังไม่พร้อม)"""
    response.headers["Cache-Control"] = "no-store"

def get_latest_gold_item():
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data:
        return {}
    if GLOBAL_CACHE["source_type"] == "Classic Website":
        return data[0]
    return data[-1]

def get_cache_age_seconds():
    last_updated = GLOBAL_CACHE.get("last_updated")
    if not last_updated:
        return None
    try:
        tz = datetime.timezone(datetime.timedelta(hours=7))
        updated_at = datetime.datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        return max(0, int((get_thai_time() - updated_at).total_seconds()))
    except Exception:
        return None

def is_data_stale():
    if not GLOBAL_CACHE["gold_bar_data"]:
        return True
    market_open, _ = is_market_open()
    if not market_open:
        return False
    age_seconds = get_cache_age_seconds()
    if age_seconds is None:
        return True
    return age_seconds > STALE_AFTER_MINUTES * 60

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
    
    # Debug Time
    # print(f"🕒 Server Thai Time: {now.strftime('%Y-%m-%d %H:%M:%S')} (Weekday: {weekday})")
    
    # วันเสาร์ (5): เปิดแค่ 09:00 - 10:00 (ขยายเวลาตามคำขอ)
    if weekday == 5:
        if datetime.time(9, 0) <= current <= datetime.time(10, 0):
            return True, "Open (Sat Morning)"
        return False, "Closed (Sat > 10:00)"

    # วันธรรมดา (0-4): เปิด 09:00 - 17:45
    if datetime.time(9, 0) <= current <= datetime.time(17, 45):
        return True, "Open (Weekday)"
        
    return False, f"Closed (Outside Hours: {current.strftime('%H:%M')})"

def is_shop_open():
    """
    เช็คเวลาทำการร้านค้า (24/7 ยกเว้นปิดเสาร์ 9:30 - จันทร์ 00:00)
    """
    now = get_thai_time()
    weekday = now.weekday()
    current_time = now.time()
    
    # Debug Shop Time
    print(f"🕒 Checker: {now.strftime('%H:%M')} | Weekday: {weekday}")

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
    # Timeout 15s -> 60s (เผื่อเว็บช้ามาก)
    await page.goto("https://www.goldtraders.or.th/updatepricelist", timeout=60000)
    # Timeout 5s -> 30s
    # NEW LOGIC: รอจนกว่าจะมีข้อมูลมากกว่า 2 แถว (Header + Data) ป้องกันการดึงว่าง
    try:
        await page.wait_for_function("document.querySelectorAll('table tbody tr').length > 2", timeout=30000)
    except:
        print("   ⚠️ Wait Timeout: Table rows did not load in time.") 

    # 1. Gold Bar
    gold_data = []
    rows = await page.locator("table tbody tr").all()
    print(f"   [Debug] New Version Found {len(rows)} rows")
    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) >= 10:
            texts = await asyncio.gather(*[cell.inner_text() for cell in cells])
            gold_data.append({
                "date": texts[0].strip(),
                "time": texts[1].strip(),
                "round": texts[2].strip(),
                "bullion_buy": texts[3].strip(),
                "bullion_sell": texts[4].strip(),
                "ornament_buy": texts[5].strip(),
                "ornament_sell": texts[6].strip(),
                "gold_spot": texts[7].strip(),
                "thb": texts[8].strip(),
                "change": texts[9].replace('\n', '').strip()
            })

    # Validation: ถ้าไม่เจอข้อมูลทองคำแท่งเลย ให้ถือว่า "ล้มเหลว" เพื่อไปใช้ Classic แทน
    if not gold_data:
        raise Exception("Zero Gold Bar rows found in New Version")

    # 2. Jewelry Percent
    jewelry_data = []
    try:
        await page.goto("https://www.goldtraders.or.th/dailyprices", timeout=30000)
        
        # Logic from User (Proven to work):
        await page.wait_for_selector("td:has-text('96.5%')", timeout=20000)
        
        # เจาะจงตารางที่มีคำว่า "96.5%" เท่านั้น
        target_table = page.locator("table").filter(has_text="96.5%")
        
        if await target_table.count() > 0:
            rows = await target_table.locator("tbody tr").all()
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
    await page.goto("https://www.goldtraders.or.th/UpdatePriceList.aspx", timeout=30000)
    await page.wait_for_selector("#DetailPlace_MainGridView", timeout=15000)

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
                except Exception as e_new:
                    print(f"   ⚠️ Discovery Mode: New Version failed ({e_new})")
                    # [DISABLED] Fallback to Classic as per user request
                    # try:
                    #     result_data = await scrape_classic_version(page)
                    # except Exception as e_classic:
                    #     print(f"   ❌ All sources failed. Classic Error: {e_classic}")

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

        # --- PHASE 4: CHECK FOR PRICE CHANGE & NOTIFY ---
        if scrape_gold and GLOBAL_CACHE["gold_bar_data"]:
            # ดึงข้อมูลราคาทองแท่งล่าสุด
            latest_data = None
            if GLOBAL_CACHE["source_type"] == "Classic Website":
                latest_data = GLOBAL_CACHE["gold_bar_data"][0]
            else:
                latest_data = GLOBAL_CACHE["gold_bar_data"][-1]
            
            current_sell = latest_data.get("bullion_sell", "").replace(",", "")
            current_ornament = latest_data.get("ornament_sell", "").replace(",", "")
            
            # ตรวจสอบว่าราคาเปลี่ยนจากครั้งก่อนหรือไม่
            if current_sell and current_sell != NOTIF_CACHE["last_gold_bar_sell"]:
                old_price = NOTIF_CACHE["last_gold_bar_sell"]
                
                # อัปเดต Cache และบันทึก State ทันที
                NOTIF_CACHE["last_gold_bar_sell"] = current_sell
                NOTIF_CACHE["last_update_time"] = latest_data.get("time", "")
                
                save_notification_state({
                    "last_gold_bar_sell": NOTIF_CACHE["last_gold_bar_sell"],
                    "last_update_time": NOTIF_CACHE["last_update_time"],
                    "last_sent_at": NOTIF_CACHE["last_sent_at"]
                })
                
                # ถ้าไม่ใช่ครั้งแรกที่รัน (old_price ไม่เป็น None) ให้ส่ง Notification
                if old_price is not None:
                    change_text = latest_data.get("change", "0")
                    # พยายามแปลงราคาให้สวยงาม
                    try:
                        price_num = "{:,}".format(int(current_sell))
                        ornament_num = "{:,}".format(int(current_ornament))
                    except:
                        price_num = current_sell
                        ornament_num = current_ornament
                        
                    title = "🔔 ปรับราคาทองคำล่าสุด!"
                    # เพิ่มราคาทองรูปพรรณใน Body ด้วย
                    body = f"ทองแท่ง: {price_num} | รูปพรรณ: {ornament_num} ({change_text})"
                    
                    # ส่งในรูปแบบ async โดยไม่รอผลกระทบต่อ scraping cycle
                    asyncio.create_task(send_push_notification(
                        title=title,
                        body=body,
                        data={
                            "price": current_sell,           # ราคาแท่ง
                            "ornament": current_ornament,    # ราคารูปพรรณ (New!)
                            "change": change_text,           # การเปลี่ยนแปลง (New!)
                            "type": "bullion",
                            "update_time": latest_data.get("time", "")
                        }
                    ))
    
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

# Allow CORS for PWA and Web Apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check(response: Response):
    """Endpoint สำหรับเช็คว่า Process ยังรันอยู่ (Liveness)"""
    set_no_store(response)
    return {
        "status": "ok",
        "service": "thai-gold-api",
        "timestamp": get_thai_time().isoformat()
    }

@app.get("/ready")
def readiness_check(response: Response):
    """Endpoint สำหรับเช็คความพร้อมของข้อมูล (Readiness)"""
    set_no_store(response)
    has_data = len(GLOBAL_CACHE["gold_bar_data"]) > 0
    if not has_data:
        response.status_code = 503
    
    return {
        "status": "ready" if has_data else "not_ready",
        "has_gold_data": has_data,
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "source": GLOBAL_CACHE["source_type"],
        "last_updated": GLOBAL_CACHE["last_updated"],
        "market_status": GLOBAL_CACHE["market_status"]
    }

@app.get("/")
def read_root(response: Response):
    set_public_cache(response, max_age=15, s_maxage=30)
    return {
        "message": "Thai Gold Price API (Hybrid Auto-Switch)",
        "source_used": GLOBAL_CACHE["source_type"],
        "market_status": GLOBAL_CACHE["market_status"],
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "last_updated": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/latest")
def get_latest(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data:
        set_no_store(response)
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}
    
    set_public_cache(response, max_age=15, s_maxage=30)
    
    # Logic เลือกข้อมูลล่าสุดตาม Source
    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "data": get_latest_gold_item(),
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/gold")
def get_gold_buy_only(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data: 
        set_no_store(response)
        return {"status": "waiting_for_data"}

    set_public_cache(response, max_age=15, s_maxage=30)

    latest = get_latest_gold_item()

    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "bullion_buy": latest.get("bullion_buy"),
        "ornament_buy": latest.get("ornament_buy"),
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/history")
def get_history(response: Response):
    set_public_cache(response, max_age=60, s_maxage=120)
    return {
        "count": len(GLOBAL_CACHE["gold_bar_data"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["gold_bar_data"],
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/percent_jewelry")
def get_percent(response: Response):
    set_public_cache(response, max_age=60, s_maxage=120)
    return {
        "count": len(GLOBAL_CACHE["jewelry_percent"]),
        "source": GLOBAL_CACHE["source_type"],
        "data": GLOBAL_CACHE["jewelry_percent"],
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/shops")
def get_shops(response: Response):
    set_public_cache(response, max_age=60, s_maxage=120)
    return {
        "count": len(GLOBAL_CACHE["shop_data"]),
        "data": GLOBAL_CACHE["shop_data"],
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "updated_at": GLOBAL_CACHE["last_updated"]
    }

@app.get("/api/board")
def get_board(response: Response):
    data = GLOBAL_CACHE["gold_bar_data"]
    if not data:
        set_no_store(response)
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}

    set_public_cache(response, max_age=15, s_maxage=30)
    history_recent = data[:20] if GLOBAL_CACHE["source_type"] == "Classic Website" else data[-20:]
    return {
        "status": "success",
        "source": GLOBAL_CACHE["source_type"],
        "market_status": GLOBAL_CACHE["market_status"],
        "updated_at": GLOBAL_CACHE["last_updated"],
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "latest": get_latest_gold_item(),
        "history": history_recent,
        "jewelry": GLOBAL_CACHE["jewelry_percent"],
        "shops": GLOBAL_CACHE["shop_data"],
        "counts": {
            "history": len(GLOBAL_CACHE["gold_bar_data"]),
            "jewelry": len(GLOBAL_CACHE["jewelry_percent"]),
            "shops": len(GLOBAL_CACHE["shop_data"])
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
