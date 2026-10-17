from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, Page
import uvicorn
import asyncio
import datetime
import random
from typing import Dict, Any, Optional, List
import os
import json
import firebase_admin
from firebase_admin import credentials, messaging
from shop import scrape_all_shops
from snapshot_state import SnapshotCommit, SnapshotStore, build_board_snapshot

# ==============================================================================
# 1. CENTRAL DATA STORE (กองกลางเก็บข้อมูล)
# ==============================================================================
GLOBAL_CACHE: Dict[str, Any] = {
    "gold_bar_data": [],      # เก็บประวัติราคาทองคำแท่ง
    "jewelry_percent": [],    # เก็บราคาทองรูปพรรณ (เฉพาะ %)
    "shop_data": [],          # เก็บข้อมูลจาก 5 ร้านทอง
    "last_updated": None,     # เวลาที่ข้อมูลธุรกิจเปลี่ยนล่าสุด
    "last_checked_at": None,  # เวลาที่ scrape สำเร็จล่าสุด
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
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
STALE_AFTER_MINUTES = int(os.getenv("STALE_AFTER_MINUTES", "10"))
FCM_TOPIC_NAME = os.getenv("FCM_TOPIC_NAME", "gold_price_updates")
FCM_SEND_TIMEOUT_SECONDS = int(os.getenv("FCM_SEND_TIMEOUT_SECONDS", "15"))
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
SNAPSHOT_STATE_FILE = os.getenv(
    "SNAPSHOT_STATE_FILE", os.path.join(os.path.dirname(STATE_FILE), "gold_snapshot_state.json")
)
SNAPSHOT_STORE = SnapshotStore(SNAPSHOT_STATE_FILE)
SNAPSHOT_UPDATE_LOCK = asyncio.Lock()
SNAPSHOT_REVISION = 0
SNAPSHOT_UPDATED_AT: Optional[str] = None
CURRENT_BOARD_SNAPSHOT: Dict[str, Any] = {}


class PushSubscriptionRequest(BaseModel):
    """A Web FCM registration token to subscribe to the fixed price-update topic."""

    token: str = Field(min_length=1, max_length=4096)
    topic: Optional[str] = None
    platform: Optional[str] = None


def restore_snapshot_state() -> None:
    """Restore the last atomic snapshot so a container restart keeps its revision."""
    global SNAPSHOT_REVISION, SNAPSHOT_UPDATED_AT, CURRENT_BOARD_SNAPSHOT
    state = SNAPSHOT_STORE.load()
    if not state:
        return

    cache = state.get("cache", {})
    for key in ("gold_bar_data", "jewelry_percent", "shop_data", "market_status", "source_type"):
        if key in cache:
            GLOBAL_CACHE[key] = cache[key]
    SNAPSHOT_REVISION = state["revision"]
    SNAPSHOT_UPDATED_AT = state.get("updated_at")
    CURRENT_BOARD_SNAPSHOT = state.get("snapshot", {})
    GLOBAL_CACHE["last_updated"] = SNAPSHOT_UPDATED_AT
    GLOBAL_CACHE["last_checked_at"] = state.get("last_checked_at", SNAPSHOT_UPDATED_AT)
    print(f"✅ [SnapshotState] Restored revision {SNAPSHOT_REVISION} from {SNAPSHOT_STATE_FILE}")


restore_snapshot_state()


def parse_allowed_origins() -> List[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


ALLOWED_ORIGINS = parse_allowed_origins()
FIREBASE_ENABLED = False
FIREBASE_STATUS = "disabled"

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
        state_dir = os.path.dirname(STATE_FILE)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
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
    "topic_name": FCM_TOPIC_NAME
}

def init_firebase():
    global FIREBASE_ENABLED, FIREBASE_STATUS

    try:
        firebase_admin.get_app()
        FIREBASE_ENABLED = True
        FIREBASE_STATUS = "ready"
        return
    except ValueError:
        pass

    try:
        if FIREBASE_CREDENTIALS_JSON:
            cred_info = json.loads(FIREBASE_CREDENTIALS_JSON)
            cred = credentials.Certificate(cred_info)
            firebase_admin.initialize_app(cred)
            FIREBASE_ENABLED = True
            FIREBASE_STATUS = "ready"
            print("✅ [Firebase] SDK Initialized Successfully (Using: FIREBASE_CREDENTIALS_JSON)")
            return

        if os.path.exists(CRED_PATH):
            cred = credentials.Certificate(CRED_PATH)
            firebase_admin.initialize_app(cred)
            FIREBASE_ENABLED = True
            FIREBASE_STATUS = "ready"
            print(f"✅ [Firebase] SDK Initialized Successfully (Using: {os.path.basename(CRED_PATH)})")
            return

        FIREBASE_STATUS = "missing_credentials"
        print(f"⚠️ [Firebase] Warning: Credentials not found at {CRED_PATH}. Push notifications disabled.")
    except Exception as e:
        FIREBASE_STATUS = "init_error"
        print(f"❌ [Firebase] Initialization Error: {e}")


init_firebase()

async def send_push_notification(title: str, body: str, data: Dict[str, str] = None):
    """ส่ง Push Notification ผ่าน FCM Topic"""
    if not FIREBASE_ENABLED:
        print("⚠️ [Push] Skipped: Firebase is not enabled.")
        return
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            topic=NOTIF_CACHE["topic_name"],
        )
        response = await asyncio.wait_for(
            asyncio.to_thread(messaging.send, message),
            timeout=FCM_SEND_TIMEOUT_SECONDS,
        )
        print(f"🔔 [Push] Sent Success: {response}")
        
        # อัปเดตสถานะการส่งสำเร็จหลังจากส่งจริงเท่านั้น
        NOTIF_CACHE["last_sent_at"] = get_thai_time().isoformat()
        save_notification_state({
            "last_gold_bar_sell": NOTIF_CACHE["last_gold_bar_sell"],
            "last_update_time": NOTIF_CACHE["last_update_time"],
            "last_sent_at": NOTIF_CACHE["last_sent_at"]
        })
    except asyncio.TimeoutError:
        print(f"❌ [Push] Send timed out after {FCM_SEND_TIMEOUT_SECONDS} seconds")
    except Exception as e:
        print(f"❌ [Push] Send Error: {e}")


async def send_snapshot_update_notification(commit: SnapshotCommit) -> None:
    """Send a data-only signal; clients fetch /api/board as the source of truth."""
    if not FIREBASE_ENABLED:
        print("⚠️ [Push] Snapshot update skipped: Firebase is not enabled.")
        return

    payload = {
        "type": "gold_snapshot_updated",
        "revision": str(commit.revision),
        "updated_at": commit.updated_at or "",
        # A visible notification is reserved for a real price-data snapshot.
        # Clients may still handle other data-only events silently.
        "user_visible": "true",
    }
    try:
        message = messaging.Message(data=payload, topic=NOTIF_CACHE["topic_name"])
        response = await asyncio.wait_for(
            asyncio.to_thread(messaging.send, message),
            timeout=FCM_SEND_TIMEOUT_SECONDS,
        )
        NOTIF_CACHE["last_sent_at"] = get_thai_time().isoformat()
        save_notification_state({
            "last_gold_bar_sell": NOTIF_CACHE["last_gold_bar_sell"],
            "last_update_time": NOTIF_CACHE["last_update_time"],
            "last_sent_at": NOTIF_CACHE["last_sent_at"],
        })
        print(f"🔔 [Push] Snapshot revision {commit.revision} sent: {response}")
    except asyncio.TimeoutError:
        print(f"❌ [Push] Snapshot revision {commit.revision} timed out after {FCM_SEND_TIMEOUT_SECONDS} seconds")
    except Exception as error:
        # The snapshot was already committed. Never roll it back for a failed push.
        print(f"❌ [Push] Snapshot revision {commit.revision} failed: {error}")

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
    last_checked_at = GLOBAL_CACHE.get("last_checked_at") or GLOBAL_CACHE.get("last_updated")
    if not last_checked_at:
        return None
    try:
        checked_at = datetime.datetime.fromisoformat(last_checked_at)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=7)))
        return max(0, int((get_thai_time() - checked_at).total_seconds()))
    except Exception:
        try:
            tz = datetime.timezone(datetime.timedelta(hours=7))
            checked_at = datetime.datetime.strptime(last_checked_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
            return max(0, int((get_thai_time() - checked_at).total_seconds()))
        except Exception:
            return None


async def commit_snapshot_if_changed(checked_at: Optional[str] = None) -> Optional[SnapshotCommit]:
    """Commit cache and revision as one unit before notifying clients."""
    global SNAPSHOT_REVISION, SNAPSHOT_UPDATED_AT, CURRENT_BOARD_SNAPSHOT
    checked_at = checked_at or get_thai_time().isoformat()
    try:
        async with SNAPSHOT_UPDATE_LOCK:
            commit = SNAPSHOT_STORE.commit(GLOBAL_CACHE, checked_at)
            SNAPSHOT_REVISION = commit.revision
            SNAPSHOT_UPDATED_AT = commit.updated_at
            CURRENT_BOARD_SNAPSHOT = build_board_snapshot(GLOBAL_CACHE)
            GLOBAL_CACHE["last_checked_at"] = checked_at
            if commit.updated_at:
                GLOBAL_CACHE["last_updated"] = commit.updated_at
            return commit
    except Exception as error:
        print(f"❌ [SnapshotState] Commit failed: {error}")
        return None


async def update_market_status(status: str) -> Optional[SnapshotCommit]:
    if GLOBAL_CACHE["market_status"] == status:
        return None
    GLOBAL_CACHE["market_status"] = status
    return await commit_snapshot_if_changed()

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
        
    return False, "Closed (Outside Market Hours)"

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

async def update_all_data(scrape_gold: bool = True, scrape_shops: bool = False) -> tuple[bool, bool]:
    global GLOBAL_CACHE
    now_str = get_thai_time().strftime('%H:%M:%S')
    gold_scrape_succeeded = False
    price_changed = False
    
    # ดึงค่า Source ที่จำไว้ (Sticky Session)
    current_source = GLOBAL_CACHE.get("source_type", "None")
    previous_latest = get_latest_gold_item().copy()
    shop_scrape_succeeded = False

    if not browser_instance: 
        print("❌ Error: Browser not running!")
        return False, False

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
                gold_scrape_succeeded = bool(result_data["gold"])
            else:
                GLOBAL_CACHE["source_type"] = "None"

        # --- PHASE 3: Shop Scraping (Parallel) - Only if requested ---
        if scrape_shops:
            print(f"🏭 [{now_str}] Scraping 5 Shops...")
            try:
                shop_results = await scrape_all_shops(context)
                GLOBAL_CACHE["shop_data"] = shop_results
                shop_scrape_succeeded = True
            except Exception as e:
                print(f"   ❌ Shop Scraping Error: {e}")

        # Commit all business data as one snapshot before emitting its FCM signal.
        if gold_scrape_succeeded or shop_scrape_succeeded:
            if gold_scrape_succeeded:
                price_changed = bool(previous_latest) and previous_latest != get_latest_gold_item()

            commit = await commit_snapshot_if_changed()
            if commit and commit.changed:
                await send_snapshot_update_notification(commit)
    
    except Exception as e:
        print(f"🔥 Critical System Error: {e}")
        GLOBAL_CACHE["source_type"] = "None"
    
    finally:
        # 🛡️ CLEANUP: Always close the context!
        await context.close()

    return gold_scrape_succeeded, price_changed

def get_gold_scrape_delay_seconds(now: datetime.datetime, unchanged_count: int, fast_poll_until: Optional[datetime.datetime]) -> int:
    """Choose a polite, market-aware interval for the next Gold Traders request."""
    current = now.time()
    weekday = now.weekday()

    if fast_poll_until and now < fast_poll_until:
        return random.randint(3 * 60, 5 * 60)

    if unchanged_count >= 3:
        return random.randint(12 * 60, 15 * 60)

    if weekday == 5:  # Saturday: short 09:00-10:00 market window
        return random.randint(5 * 60, 8 * 60)

    is_opening_window = datetime.time(9, 0) <= current < datetime.time(9, 20)
    is_closing_window = datetime.time(17, 25) <= current <= datetime.time(17, 45)
    if is_opening_window or is_closing_window:
        return random.randint(4 * 60, 6 * 60)

    return random.randint(7 * 60, 10 * 60)


async def run_scheduler(initial_scrape_completed: bool = False):
    next_gold_scrape_at: Optional[datetime.datetime] = None
    unchanged_count = 0
    consecutive_failures = 0
    fast_poll_until: Optional[datetime.datetime] = None
    next_closed_log_at: Optional[datetime.datetime] = None

    if initial_scrape_completed:
        now = get_thai_time()
        next_gold_scrape_at = now + datetime.timedelta(
            seconds=get_gold_scrape_delay_seconds(now, unchanged_count, fast_poll_until)
        )

    while True:
        now = get_thai_time()
        is_open, status_msg = is_market_open()
        status_commit = await update_market_status(f"{status_msg} | Shop scraping disabled")
        if status_commit and status_commit.changed:
            # A status label naturally changes at midnight (for example from
            # Friday to Saturday). Persist its revision, but do not wake every
            # subscriber with a price notification when no price changed.
            print(f"ℹ️ [Snapshot] Market status revision {status_commit.revision} saved without push")

        if not is_open:
            next_gold_scrape_at = None
            await stop_browser()
            if not next_closed_log_at or now >= next_closed_log_at:
                print(f"💤 Market Closed ({GLOBAL_CACHE['market_status']}) - RAM Saved!")
                next_closed_log_at = now + datetime.timedelta(hours=1)
            await asyncio.sleep(60)
            continue

        next_closed_log_at = None
        if next_gold_scrape_at is None:
            next_gold_scrape_at = now

        if now >= next_gold_scrape_at:
            await start_browser()
            succeeded, price_changed = await update_all_data(scrape_gold=True, scrape_shops=False)
            await stop_browser()
            now = get_thai_time()

            if succeeded:
                consecutive_failures = 0
                unchanged_count = 0 if price_changed else unchanged_count + 1
                if price_changed:
                    fast_poll_until = now + datetime.timedelta(minutes=20)
            else:
                consecutive_failures += 1
                unchanged_count = 0

            if consecutive_failures:
                delay_seconds = min(40 * 60, 10 * 60 * (2 ** (consecutive_failures - 1)))
                print(f"⚠️ Gold scrape failed; retrying in {delay_seconds // 60} minutes")
            else:
                delay_seconds = get_gold_scrape_delay_seconds(now, unchanged_count, fast_poll_until)
                print(f"🕒 Next Gold Traders scrape in {delay_seconds // 60} minutes")

            next_gold_scrape_at = now + datetime.timedelta(seconds=delay_seconds)
            continue

        wait_seconds = max(1, min(60, int((next_gold_scrape_at - now).total_seconds())))
        await asyncio.sleep(wait_seconds)

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
        is_open, status_msg = is_market_open()
        GLOBAL_CACHE["market_status"] = f"{status_msg} | Shop scraping disabled"
        await start_browser()
        
        # Force Scrape: บังคับดึงข้อมูล 1 รอบตอนเปิด Server เสมอ (ไม่สนตลาดเปิด/ปิด)
        # เพื่อให้มีข้อมูลใน Cache ไปแสดงผล (จะได้ไม่ขึ้น waiting_for_data)
        await update_all_data(scrape_gold=True, scrape_shops=False)
        await stop_browser()
        
        # เริ่ม Scheduler หลังจาก Initial Scrape เสร็จ
        asyncio.create_task(run_scheduler(initial_scrape_completed=True))

    asyncio.create_task(initial_startup())
    
    yield
    
    print("🛑 System Stopping...")
    await stop_browser()

app = FastAPI(lifespan=lifespan)

# Allow CORS for PWA and Web Apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
        "timestamp": get_thai_time().isoformat(),
        "firebase_enabled": FIREBASE_ENABLED,
        "firebase_status": FIREBASE_STATUS,
        "cors_origins": ALLOWED_ORIGINS,
        "notification_topic": NOTIF_CACHE["topic_name"],
        "state_file": STATE_FILE
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
        "market_status": GLOBAL_CACHE["market_status"],
        "firebase_enabled": FIREBASE_ENABLED,
        "firebase_status": FIREBASE_STATUS
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


@app.post("/api/push/subscribe", status_code=202)
async def subscribe_web_push(subscription: PushSubscriptionRequest, response: Response):
    """Subscribe a browser's FCM token to the server-controlled update topic."""
    set_no_store(response)

    if not FIREBASE_ENABLED:
        raise HTTPException(status_code=503, detail="Push notifications are unavailable")

    token = subscription.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="A non-empty FCM token is required")

    if subscription.platform and subscription.platform.lower() != "web":
        raise HTTPException(status_code=422, detail="Only web push subscriptions are supported")

    # Clients may only join the price-update topic. Do not allow arbitrary topic names.
    if subscription.topic and subscription.topic != FCM_TOPIC_NAME:
        raise HTTPException(status_code=422, detail="Unsupported notification topic")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(messaging.subscribe_to_topic, [token], FCM_TOPIC_NAME),
            timeout=FCM_SEND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=504, detail="Push subscription timed out") from error
    except Exception as error:
        print(f"⚠️ [Push] Web subscription failed: {type(error).__name__}")
        raise HTTPException(status_code=502, detail="Push subscription failed") from error

    if result.failure_count:
        print(f"⚠️ [Push] Web subscription rejected ({result.failure_count} token failure)")
        raise HTTPException(status_code=422, detail="FCM token was rejected")

    return {"status": "subscribed", "topic": FCM_TOPIC_NAME, "platform": "web"}


def get_board_etag() -> Optional[str]:
    if SNAPSHOT_REVISION <= 0:
        return None
    return f'W/"gold-board-{SNAPSHOT_REVISION}"'


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    if not if_none_match:
        return False
    return if_none_match.strip() == "*" or etag in {
        candidate.strip() for candidate in if_none_match.split(",")
    }


@app.get("/api/board")
def get_board(request: Request, response: Response):
    snapshot = CURRENT_BOARD_SNAPSHOT
    if not snapshot:
        set_no_store(response)
        return {"status": "waiting_for_data", "market_status": GLOBAL_CACHE["market_status"]}

    etag = get_board_etag()
    cache_control = "private, no-cache, must-revalidate"
    if etag and etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})

    response.headers["Cache-Control"] = cache_control
    if etag:
        response.headers["ETag"] = etag

    return {
        "status": "ok",
        "revision": SNAPSHOT_REVISION,
        "source": snapshot["source"],
        "market_status": snapshot["market_status"],
        "updated_at": SNAPSHOT_UPDATED_AT,
        "stale": is_data_stale(),
        "age_seconds": get_cache_age_seconds(),
        "latest": snapshot["latest"],
        "history": snapshot["history"],
        "jewelry": snapshot["jewelry"],
        "shops": snapshot["shops"],
        "counts": snapshot["counts"],
    }

if __name__ == "__main__":
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
