import os
import asyncio
import re
import sys
import subprocess
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pyvirtualdisplay import Display

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
        self.chat_id = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        self.bruteforce_selectors = [
            "form[name='F1']", "text='Download File'", "#downloadButton", 
            ".downloadbtn", "button:has-text('Download')", "a:has-text('Download')",
            "i.fa-download", "#filemanager_itemslist button", "a[href*='download']"
        ]
        self.combined_query = ", ".join(self.bruteforce_selectors)

    async def _notify(self, text):
        print(f"[*] {text}")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
        except: pass

    async def _send_screenshot(self, file_path, caption):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            with open(file_path, "rb") as photo:
                requests.post(url, data={"chat_id": self.chat_id, "caption": caption}, files={"photo": photo}, timeout=15)
        except Exception as e:
            print(f"[!] Gagal kirim screenshot: {e}")

    async def _generic_browser_handler(self, page):
        # Scan cuma sekali, nunggu maksimal 10 detik
        await self._notify("🔎 Mencari tombol (Limit 10 detik)...")
        
        try:
            # TUNGGU STRICT 10 DETIK
            await page.wait_for_selector(self.combined_query, state="visible", timeout=10000)
            
            # Cari mana yang beneran nongol
            target_el = None
            for sel in self.bruteforce_selectors:
                el = page.locator(sel).first
                if await el.is_visible():
                    target_el = el
                    break

            if target_el:
                async with page.expect_download(timeout=30000) as download_info:
                    tag = await target_el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "form":
                        await target_el.evaluate("el => el.submit()")
                    else:
                        await target_el.click(force=True)
                
                download = await download_info.value
                save_path = os.path.join(os.getcwd(), download.suggested_filename)
                await download.save_as(save_path)
                return save_path

        except PlaywrightTimeout:
            await self._notify("⏰ Timeout 10 detik! Tombol gak ketemu.")
            shot_path = "timeout_error.png"
            await page.screenshot(path=shot_path)
            await self._send_screenshot(shot_path, f"❌ Timeout 10s di URL: {self.url}")
            return None
        except Exception as e:
            await self._notify(f"⚠️ Error: {str(e)[:50]}")
            return None

    async def run(self):
        display = Display(visible=0, size=(1366, 768))
        display.start()

        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
                
                # Kasih napas dikit buat JS render (tapi gak nunggu semenit)
                await asyncio.sleep(2)

                result = await self._generic_browser_handler(page)
                if result:
                    await self._notify(f"✅ BERHASIL: {result}")
                else:
                    sys.exit(1)
            finally:
                await browser.close()
                display.stop()

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
