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
        # Ambil dari Environment Variables
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("OWNER_ID")
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # Selector CSS valid untuk nunggu (No syntax error "=")
        self.wait_query = "button, a, form, .btn, .downloadbtn, #downloadButton, i.fa-download"
        
        # List deteksi tombol download
        self.check_selectors = [
            "form[name='F1']",
            "text=/Download/i",
            "text=/Get File/i",
            "#downloadButton",
            ".downloadbtn",
            "i.fa-download",
            "a[href*='download']",
            ".btn-primary"
        ]

    async def _notify(self, text):
        print(f"[*] TG_LOG: {text}")
        if not self.bot_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": int(self.chat_id), "text": text}, timeout=10)
        except Exception as e: print(f"[!] Gagal notify: {e}")

    async def _send_html_source(self, html_content):
        """Kirim source code HTML sebagai file dokumen ke Telegram."""
        if not self.bot_token or not self.chat_id: return
        filename = "debug_source.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        try:
            with open(filename, "rb") as doc:
                res = requests.post(
                    url, 
                    data={"chat_id": int(self.chat_id), "caption": f"📄 HTML Source: {self.url}"}, 
                    files={"document": doc}, 
                    timeout=30
                )
                print(f"[*] TG Document Response: {res.text}")
        except Exception as e:
            print(f"[!] Gagal kirim HTML: {e}")
        print(f"{html_content}")

    async def _generic_browser_handler(self, page):
        await self._notify("🔎 Mencari tombol (Limit 10 detik)...")
        
        try:
            # Tunggu element dasar muncul
            await page.wait_for_selector(self.wait_query, state="visible", timeout=10000)
            
            target_el = None
            for sel in self.check_selectors:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    target_el = el
                    break

            if target_el:
                await self._notify("🎯 Ketemu! Memulai download...")
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
            else:
                raise PlaywrightTimeout("Selector dasar ada, tapi gak ada yang cocok buat diklik.")

        except PlaywrightTimeout:
            await self._notify("⏰ Timeout 10s: Tombol tidak ditemukan.")
            # Ambil HTML full buat dianalisa
            html_content = await page.content()
            await self._send_html_source(html_content)
            return None
        except Exception as e:
            await self._notify(f"⚠️ Error: {str(e)[:100]}")
            return None

    async def run(self):
        # Pakai Xvfb dari workflow (headless=False)
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                # Gofile butuh waktu extra buat fetch list file via API
                await asyncio.sleep(5)

                result = await self._generic_browser_handler(page)
                if result:
                    await self._notify(f"✅ SUKSES: {result}")
                else:
                    # Tunggu dikit biar pengiriman dokumen gak keputus
                    await asyncio.sleep(5)
                    sys.exit(1)
            finally:
                await browser.close()

if __name__ == "__main__":
    url = os.environ.get("PAYLOAD_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not url: sys.exit(1)
    asyncio.run(DownloaderBot(url).run())
