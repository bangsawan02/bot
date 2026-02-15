import os
import asyncio
import re
import sys
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        # Ambil dari Env yang lu set di GitHub Workflow
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("OWNER_ID") # Pakai OWNER_ID dari env log lu
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        self.wait_query = "button, a, form[name='F1'], .downloadbtn, #downloadButton, i.fa-download, .btn-primary"
        self.check_selectors = [
            "form[name='F1']", "text=/Download/i", "#downloadButton", 
            ".downloadbtn", "i.fa-download", "a[href*='download']", ".btn-primary"
        ]

    async def _notify(self, text):
        print(f"[*] TG_LOG: {text}")
        if not self.bot_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": int(self.chat_id), "text": text}, timeout=10)
        except Exception as e: print(f"Error TG: {e}")

    async def _send_screenshot(self, file_path, caption):
        if not os.path.exists(file_path): return
        print(f"[*] Mengirim screenshot ke {self.chat_id}...")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            with open(file_path, "rb") as photo:
                res = requests.post(
                    url, 
                    data={"chat_id": int(self.chat_id), "caption": caption}, 
                    files={"photo": photo}, 
                    timeout=30
                )
                print(f"[*] TG Response: {res.text}")
        except Exception as e: print(f"Error Upload: {e}")

    async def _generic_browser_handler(self, page):
        await self._notify("🔎 Mencari tombol (10s limit)...")
        try:
            # Gofile butuh waktu buat narik data API, 10s mungkin mepet di CI
            await page.wait_for_selector(self.wait_query, state="visible", timeout=10000)
            
            target_el = None
            for sel in self.check_selectors:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    target_el = el
                    break

            if target_el:
                await self._notify("🎯 Tombol ditemukan! Downloading...")
                async with page.expect_download(timeout=60000) as download_info:
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
            shot_path = "timeout_error.png"
            await page.screenshot(path=shot_path, full_page=True)
            await self._notify("⏰ Timeout! Tombol Gak Ketemu.")
            await self._send_screenshot(shot_path, f"❌ Gagal di: {self.url}")
            await asyncio.sleep(2) # Kasih napas buat upload
            return None

    async def run(self):
        # DI SINI: Tidak perlu Display().start() karena sudah ada xvfb-run di workflow
        async with Stealth().use_async(async_playwright()) as p:
            # Pakai headless=False karena xvfb-run sudah menangani virtual display
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5) # Tambahin biar Gofile sempet loading

                result = await self._generic_browser_handler(page)
                if result:
                    await self._notify(f"✅ SUKSES: {result}")
                else:
                    sys.exit(1)
            finally:
                await browser.close()

if __name__ == "__main__":
    url = os.environ.get("PAYLOAD_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url: sys.exit(1)
    asyncio.run(DownloaderBot(url).run())
