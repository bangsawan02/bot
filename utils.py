import os
import asyncio
import re
import sys
import subprocess
import requests
import time
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pyvirtualdisplay import Display

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        # Pastikan environment variable ini terisi di GitHub Secret atau Env lu
        self.bot_token = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
        self.chat_id = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # Selector CSS valid untuk wait
        self.wait_query = "button, a, form[name='F1'], .downloadbtn, #downloadButton, i.fa-download"
        
        self.check_selectors = [
            "form[name='F1']",
            "text=/Download/i",
            "#downloadButton",
            ".downloadbtn",
            "i.fa-download",
            "a[href*='download']"
        ]

    async def _notify(self, text):
        print(f"[*] TG_LOG: {text}")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            # Jalankan di executor agar tidak memblock loop async
            await asyncio.get_event_loop().run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10))
        except Exception as e:
            print(f"[!] Gagal kirim pesan: {e}")

    async def _send_screenshot(self, file_path, caption):
        print(f"[*] Menyiapkan pengiriman screenshot: {file_path}")
        if not os.path.exists(file_path):
            print("[!] File screenshot tidak ditemukan di disk!")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            def upload():
                with open(file_path, "rb") as photo:
                    return requests.post(
                        url, 
                        data={"chat_id": self.chat_id, "caption": caption}, 
                        files={"photo": photo}, 
                        timeout=30
                    )
            
            res = await asyncio.get_event_loop().run_in_executor(None, upload)
            if res.status_code == 200:
                print("[*] Screenshot berhasil terkirim ke Telegram.")
            else:
                print(f"[!] TG Response Error: {res.text}")
        except Exception as e:
            print(f"[!] Error saat upload ke TG: {e}")

    async def _generic_browser_handler(self, page):
        await self._notify("🔎 Mencari tombol (Limit 10 detik)...")
        
        try:
            # Tunggu tombol muncul
            await page.wait_for_selector(self.wait_query, state="visible", timeout=10000)
            
            target_el = None
            for sel in self.check_selectors:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    target_el = el
                    break

            if target_el:
                await self._notify("🎯 Tombol ditemukan! Klik...")
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
            else:
                raise PlaywrightTimeout("Selector detected but not matching criteria.")

        except PlaywrightTimeout:
            await self._notify("⏰ Timeout 10s: Tombol tidak ditemukan.")
            shot_path = "timeout_error.png"
            # Pastikan screenshot selesai ditulis sebelum lanjut
            await page.screenshot(path=shot_path, full_page=True)
            print("[*] Screenshot diambil.")
            await self._send_screenshot(shot_path, f"❌ Gagal nemu tombol di: {self.url}")
            # Kasih jeda dikit biar upload-nya gak keputus sys.exit
            await asyncio.sleep(5) 
            return None
        except Exception as e:
            await self._notify(f"⚠️ Handler Error: {str(e)[:100]}")
            return None

    async def run(self):
        # Gunakan Xvfb untuk headful simulation
        display = Display(visible=0, size=(1366, 768))
        display.start()

        async with Stealth().use_async(async_playwright()) as p:
            # Gunakan headless=False + Xvfb agar lebih mirip manusia
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=40000)
                
                # Gofile butuh waktu buat narik API list filenya
                await asyncio.sleep(3)

                result = await self._generic_browser_handler(page)
                if result:
                    await self._notify(f"✅ SUKSES: {result}")
                    if os.path.exists("timeout_error.png"): os.remove("timeout_error.png")
                else:
                    # Beri waktu tambahan sebelum exit agar pengiriman pesan selesai
                    await asyncio.sleep(2)
                    sys.exit(1)
            finally:
                await browser.close()
                display.stop()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <URL>")
        sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
