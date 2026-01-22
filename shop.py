import asyncio
from playwright.async_api import Page, BrowserContext, TimeoutError
from typing import Dict, Any, List

TIMEOUT_MS = 60000


# --- Optimized Resource Blocker ---
async def block_heavy_resources(page: Page):
    await page.route("**/*", lambda route: route.abort() 
        if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
        else route.continue_()
    )

from urllib.parse import quote

async def scrape_aurora(context: BrowserContext) -> Dict[str, Any]:
    raw_url = "https://www.aurora.co.th/price/gold_pricelist/ราคาทองวันนี้"
    safe_url = "https://www.aurora.co.th/price/gold_pricelist/" + quote("ราคาทองวันนี้")

    print(f"   >> Starting Aurora (Solo Mode)")
    result = {"name": "Aurora", "data": {}, "error": None}

    page = await context.new_page()

    # ❌ ห้าม block CSS / font สำหรับ Aurora
    await page.route("**/*", lambda route: route.abort()
        if route.request.resource_type in ["image", "media"]
        else route.continue_()
    )

    try:
        last_error = None

        for attempt in range(1, 4):
            try:
                print(f"   🔁 Aurora attempt {attempt}/3")

                try:
                    await page.goto(raw_url, timeout=90000, wait_until="domcontentloaded")
                except:
                    print("   ⚠️ Raw URL failed, retry encoded URL...")
                    await page.goto(safe_url, timeout=90000, wait_until="domcontentloaded")

                # กัน Cloudflare JS lag
                await asyncio.sleep(4)

                # soft wait table
                try:
                    await page.wait_for_selector("table tbody tr", timeout=20000)
                except:
                    print("   ⚠️ Aurora table slow, continue anyway")

                if await page.locator("table tbody tr").count() == 0:
                    raise Exception("Aurora table not found")

                row = page.locator("table tbody tr").first
                tds = row.locator("td")

                time_update = (await tds.nth(0).inner_text()).strip()
                bar_buy = (await tds.nth(2).inner_text()).strip()
                bar_sell = (await tds.nth(3).inner_text()).strip()
                ornament_buy = (await tds.nth(4).inner_text()).strip()

                result["data"] = {
                    "time": time_update,
                    "gold_bar_965": {"buy": bar_buy, "sell": bar_sell},
                    "gold_ornament_965": {"buy": ornament_buy}
                }

                print(f"   [OK] Aurora Finished")
                return result

            except Exception as e:
                last_error = str(e)
                print(f"   ⚠️ Aurora attempt {attempt} failed: {e}")

                # backoff หนักขึ้นเรื่อย ๆ
                await asyncio.sleep(10 * attempt)

        # ถ้าหมด 3 รอบแล้วยังไม่รอด
        result["error"] = last_error or "Aurora failed after retries"
        print(f"   [X] Aurora Failed after 3 attempts")

    finally:
        await page.close()

    return result


async def scrape_mts_gold(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 2: MTS Gold"""
    url = "https://www.mtsgold.co.th/mts-price-sm/"
    print(f"   >> Starting MTS Gold ({url})")
    
    result = {"name": "MTS Gold", "data": {}, "error": None}
    page = await context.new_page()
    await block_heavy_resources(page) # Block images/fonts
    
    
    try:
        await page.goto(url, timeout=TIMEOUT_MS)
        await asyncio.sleep(3)
        
        await page.wait_for_selector("#buy965mts", timeout=TIMEOUT_MS)
        
        # 1. ทองคำแท่ง 96.5%
        buy_965 = await page.locator("#buy965mts").inner_text()
        sell_965 = await page.locator("#sell965mts").inner_text()
        
        # 2. ทองคำแท่ง 99.99%
        buy_9999 = await page.locator("#buy9999mts").inner_text()
        sell_9999 = await page.locator("#sell9999mts").inner_text()
        
        result["data"] = {
            "gold_bar_965": {"buy": buy_965.strip(), "sell": sell_965.strip()},
            "gold_bar_9999": {"buy": buy_9999.strip(), "sell": sell_9999.strip()}
        }
        
        # 3. ทองรูปพรรณ (รับซื้อคืน)
        if await page.locator("#sell965gold").is_visible():
            ornament_baht = await page.locator("#sell965gold").inner_text()
            ornament_gram = await page.locator("#sell965grm").inner_text()
            result["data"]["ornament_buy_back"] = {
                "baht": ornament_baht.strip(), 
                "gram": ornament_gram.strip()
            }
            
        print(f"   [OK] MTS Gold Finished")
        
    except Exception as e:
        print(f"   [X] MTS Gold Error: {e}")
        result["error"] = str(e)
    finally:
        await page.close()
        
    return result

async def scrape_hua_seng_heng(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 3: Hua Seng Heng"""
    url = "https://www.huasengheng.com"
    print(f"   >> Starting Hua Seng Heng ({url})")
    
    result = {"name": "Hua Seng Heng", "data": {}, "error": None}
    page = await context.new_page()
    await block_heavy_resources(page) # Block images/fonts
    
    try:
        # ปรับจูน HSH: รอแค่ DOM Ready พอ (ไม่ต้องรอ load) เพื่อลดโอกาส Crash
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        
        # ใส่ anti-crash sleep เล็กน้อย
        await asyncio.sleep(2)
        
        # เช็คว่ามีข้อมูลไหมก่อนดึง (ใช้การดึงแบบ Safe Mode)
        if await page.locator("#bid965").count() > 0:
             # 1. ทองคำแท่ง 96.5%
            buy_965 = await page.locator("#bid965").first.text_content()
            sell_965 = await page.locator("#ask965").first.text_content()
            
            result["data"] = {
                "gold_bar_965": {"buy": buy_965.strip(), "sell": sell_965.strip()}
            }
            
            # 2. ทองรูปพรรณ
            if await page.locator("#bidjewelry").count() > 0:
                buy_jewel = await page.locator("#bidjewelry").first.text_content()
                sell_jewel = await page.locator("#askjewelry").first.text_content()
                result["data"]["ornament_965"] = {
                    "buy": buy_jewel.strip(),
                    "sell": sell_jewel.strip()
                }

            # 3. ทองคำแท่ง 99.99%
            if await page.locator("#bid9999").count() > 0:
                buy_9999 = await page.locator("#bid9999").first.text_content()
                sell_9999 = await page.locator("#ask9999").first.text_content()
                result["data"]["gold_bar_9999"] = {
                    "buy": buy_9999.strip(),
                    "sell": sell_9999.strip()
                }
        else:
             print("   ⚠️ Praw: HSH Element not found (Possible anti-bot or blocked)")
             result["error"] = "Element not found (possible block)"
            
        print(f"   [OK] Hua Seng Heng Finished")
        
    except Exception as e:
        print(f"   [X] Hua Seng Heng Error: {e}")
        result["error"] = str(e)
    finally:
        await page.close()

    return result

async def scrape_chin_hua_heng(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 4: Chin Hua Heng"""
    url = "https://chinhuaheng.com/gold"
    print(f"   >> Starting Chin Hua Heng ({url})")
    
    result = {"name": "Chin Hua Heng", "data": {}, "error": None}
    page = await context.new_page()
    await block_heavy_resources(page) # Block images/fonts
    
    try:
        await page.goto(url, timeout=TIMEOUT_MS)
        await asyncio.sleep(5)
        
        try:
            await page.wait_for_selector("#gpb-chh-offer", state="visible", timeout=TIMEOUT_MS)
            
            # 1. ราคาร้าน (ทองคำแท่ง 96.5%)
            chh_sell = await page.locator("#gpb-chh-offer").inner_text()
            chh_buy = await page.locator("#gpb-chh-bid").inner_text()
            
            result["data"] = {
                 "gold_bar_965": {"buy": chh_buy.strip(), "sell": chh_sell.strip()}
            }
            
            # 2. ทองคำแท่ง 99.99%
            if await page.locator("#g99Offer").is_visible():
                g99_sell = await page.locator("#g99Offer").inner_text()
                g99_buy = await page.locator("#g99Bid").inner_text()
                result["data"]["gold_bar_9999"] = {
                    "buy": g99_buy.strip(),
                    "sell": g99_sell.strip()
                }

            # 3. ทองรูปพรรณ 96.5% (บาทละ)
            if await page.locator("#g965Bath").is_visible():
                ornament_sell = await page.locator("#g965Bath").inner_text()
                result["data"]["ornament_965"] = {"sell": ornament_sell.strip()}

            print(f"   [OK] Chin Hua Heng Finished")

        except Exception as e:
             print(f"   [X] Chin Hua Heng Error (Timeout/Structure): {e}")
             result["error"] = str(e)

    except Exception as e:
        print(f"   [X] Chin Hua Heng Error: {e}")
        result["error"] = str(e)
    finally:
        await page.close()

    return result

async def scrape_ausiris(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 5: Ausiris"""
    url = "http://www.ausiris.co.th/content/index/goldprice.html"
    print(f"   >> Starting Ausiris ({url})")
    
    result = {"name": "Ausiris", "data": {}, "error": None}
    page = await context.new_page()
    await block_heavy_resources(page) # Block images/fonts
    
    try:
        # ปรับจูน: รอแค่ DOM Ready (แก้ Timeout)
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        
        # รอ 15 วินาที (Ausiris มี Loading Spinner นาน)
        await asyncio.sleep(15)
        
        # Safe Check
        if await page.locator("#G965B_bid").count() > 0:
            # 1. ทอง 96.5% ร้าน
            shop_buy_price = await page.locator("#G965B_bid").inner_text()
            shop_sell_price = await page.locator("#G965B_offer").inner_text()
            
            result["data"] = {
                "gold_bar_965": {
                    "buy": shop_buy_price.strip(),
                    "sell": shop_sell_price.strip()
                }
            }
            
            # 2. ทอง 99.99%
            if await page.locator("#G9999B_bid").is_visible():
                buy_9999 = await page.locator("#G9999B_bid").inner_text()
                sell_9999 = await page.locator("#G9999B_offer").inner_text()
                result["data"]["gold_bar_9999"] = {
                    "buy": buy_9999.strip(),
                    "sell": sell_9999.strip()
                }
        else:
             print("   ⚠️ Ausiris Element not found (Possible anti-bot or blocked)")
             result["error"] = "Element not found"

        print(f"   [OK] Ausiris Finished")
            
    except Exception as e:
        print(f"   [X] Ausiris Error: {e}")
        result["error"] = str(e)
    finally:
        await page.close()

    return result

async def scrape_all_shops(context: BrowserContext) -> List[Dict[str, Any]]:
    print("\n>> Starting Parallel Scraping for 5 Shops...")
    start_time = asyncio.get_event_loop().time()
    
    results = await asyncio.gather(
        scrape_aurora(context),
        scrape_mts_gold(context),
        scrape_hua_seng_heng(context),
        scrape_chin_hua_heng(context),
        scrape_ausiris(context)
    )
    
    end_time = asyncio.get_event_loop().time()
    duration = end_time - start_time
    print(f"\n[DONE] All shops finished in {duration:.2f} seconds.")
    
    return results
