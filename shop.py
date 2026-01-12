import asyncio
from playwright.async_api import Page, BrowserContext, TimeoutError
from typing import Dict, Any, List

TIMEOUT_MS = 60000

async def scrape_aurora(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 1: Aurora"""
    url = "https://www.aurora.co.th/price/gold_pricelist/ราคาทองวันนี้"
    print(f"   >> Starting Aurora")
    
    result = {"name": "Aurora", "data": {}, "error": None}
    page = await context.new_page()
    
    try:
        await page.goto(url, timeout=TIMEOUT_MS)
        await asyncio.sleep(3)
        
        await page.wait_for_selector(".goldden_out h3.g-price", timeout=TIMEOUT_MS)
        sell_price = await page.locator(".goldden_out h3.g-price").inner_text()
        buy_price = await page.locator(".goldden_in h3.g-price").inner_text()
        
        result["data"] = {
            "gold_bar_965": {
                "buy": buy_price.strip(),
                "sell": sell_price.strip()
            }
        }
        print(f"   [OK] Aurora Finished")
        
    except Exception as e:
        print(f"   [X] Aurora Error: {e}")
        result["error"] = str(e)
    finally:
        await page.close()
        
    return result

async def scrape_mts_gold(context: BrowserContext) -> Dict[str, Any]:
    """ร้านที่ 2: MTS Gold"""
    url = "https://www.mtsgold.co.th/mts-price-sm/"
    print(f"   >> Starting MTS Gold ({url})")
    
    result = {"name": "MTS Gold", "data": {}, "error": None}
    page = await context.new_page()
    
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
    
    try:
        await page.goto(url, timeout=TIMEOUT_MS)
        await asyncio.sleep(5)
        
        await page.wait_for_selector("#bid965", timeout=TIMEOUT_MS)
        
        # 1. ทองคำแท่ง 96.5%
        buy_965 = await page.locator("#bid965").first.inner_text()
        sell_965 = await page.locator("#ask965").first.inner_text()
        
        result["data"] = {
            "gold_bar_965": {"buy": buy_965.strip(), "sell": sell_965.strip()}
        }
        
        # 2. ทองรูปพรรณ
        if await page.locator("#bidjewelry").first.is_visible():
            buy_jewel = await page.locator("#bidjewelry").first.inner_text()
            sell_jewel = await page.locator("#askjewelry").first.inner_text()
            result["data"]["ornament_965"] = {
                "buy": buy_jewel.strip(),
                "sell": sell_jewel.strip()
            }

        # 3. ทองคำแท่ง 99.99%
        if await page.locator("#bid9999").first.is_visible():
            buy_9999 = await page.locator("#bid9999").first.inner_text()
            sell_9999 = await page.locator("#ask9999").first.inner_text()
            result["data"]["gold_bar_9999"] = {
                "buy": buy_9999.strip(),
                "sell": sell_9999.strip()
            }
            
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
    
    try:
        await page.goto(url, timeout=TIMEOUT_MS)
        
        # รอ 15 วินาที
        # print("   ...Ausiris waiting 15s...")
        await asyncio.sleep(15)
        
        try:
            await page.wait_for_selector("#G965B_bid", timeout=TIMEOUT_MS)
            
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
            
            print(f"   [OK] Ausiris Finished")
            
        except Exception:
            print("   [X] Ausiris: Not found after wait")
            result["error"] = "Table not found"
        
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
