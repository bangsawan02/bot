import os
import re
import sys
import asyncio
import tempfile
import uuid
import subprocess
import requests
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth  # Import class Stealth (pake S gede)

# -------------------------
# 🛠️ Helpers
# -------------------------
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    return name[:200] if name else f"file_{uuid.uuid4().hex[:8]}"

def extract_filename(cd: str, url: str):
    if cd:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, flags=re.IGNORECASE)
        if m: return sanitize_filename(m.group(1))
        m2 = re.search(r'filename="?([^\";]+)"?', cd)
        if m2: return sanitize_filename(m2.group(1))
    return sanitize_filename(os.path.basename(urlparse(url).path) or "downloaded_file")

# -------------------------
# 📢 Telegram Sync Helpers
# -------------------------
def _tg_send_message(bot_token, chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", 
                      json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def _tg_send_document(bot_token, chat_id, file_path, caption):
    try:
        with open(file_path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{bot_token}/sendDocument", 
                          data={"chat_id": chat_id, "caption": caption}, files={"document": f}, timeout=120)
    except: pass

# -------------------------
# 🎯 Core: Click and Capture
# -------------------------
async def click_and_capture(page, selector, timeout=30000):
    locator = page.locator(selector).first
    if not await locator.count():
        return None

    async def force_click():
        try:
            # Pake JS click biar nembus overlay iklan
            await locator.evaluate("el => el.click()")
        except:
            await locator.click(force=True)

    try:
        # Listening download & popup barengan
        async with page.context.expect_page(timeout=5000) as p_info:
            async with page.expect_download(timeout=timeout) as d_info:
                await force_click()
            
            download = await d_info.value
            fname = extract_filename(None, download.suggested_filename)
            await download.save_as(fname)
            return fname

    except PlaywrightTimeout:
        try:
            # Kalo timeout, tutup popup iklan yang mungkin muncul
            popup = await p_info.value
            await popup.close() 
            # Coba klik lagi sekali
            async with page.expect_download(timeout=timeout) as d_info_final:
                await force_click()
            download = await d_info_final.value
            fname = extract_filename(None, download.suggested_filename)
            await download.save_as(fname)
            return fname
        except:
            return None

# -------------------------
# 🤖 Main Downloader Class
# -------------------------
class DownloaderBot:
    def __init__(self, url: str):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("PAYLOAD_SENDER")
        self.selectors = [
            "#body > div > div.file_preview_row.svelte-jngqwx > div.file_preview.svelte-jngqwx.checkers > div.block.svelte-40do4p > div > button",
            "text=/.*[Dd]ownload.*/i",
            "button:has-text('Start')",
            "a[href$='.apk']"
        ]

    async def notify(self, text):
        if self.bot_token and self.chat_id:
            await asyncio.to_thread(_tg_send_message, self.bot_token, self.chat_id, text)

    async def send_file(self, path):
        if self.bot_token and self.chat_id:
            await asyncio.to_thread(_tg_send_document, self.bot_token, self.chat_id, path, f"✅ Sukses: `{path}`")

    async def _run_logic(self, playwright_instance, headless=True):
        # Di sini kita pake instance 'p' yang sudah dibungkus Stealth
        browser = await playwright_instance.chromium.launch(
            headless=headless, 
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        await self.notify(f"🌐 Loading: {self.url}")
        await page.goto(self.url, wait_until="domcontentloaded")
        
        # Bypass timer/protection
        await asyncio.sleep(8)

        # Cari tombol
        found_selector = None
        for sel in self.selectors:
            try:
                if await page.locator(sel).first.is_visible():
                    found_selector = sel
                    break
            except: continue

        if not found_selector:
            await self.notify("❌ Gagal: Tombol download nggak kelihatan.")
            await browser.close()
            return None

        await self.notify(f"🎯 Tombol ketemu! Mencoba download...")
        filename = await click_and_capture(page, found_selector)
        
        await browser.close()
        return filename

    async def run(self):
        # 🟢 INI POLA YANG LU MAU: Global Stealth wrapper
        async with Stealth().use_async(async_playwright()) as p:
            # 1. Coba Headless
            try:
                result = await self._run_logic(p, headless=True)
                if result: return result
            except Exception as e:
                print(f"Headless error: {e}")

            # 2. Fallback Xvfb (Buat di GitHub Runner/Server)
            await self.notify("🔄 Headless gagal, pindah ke Virtual Display...")
            xvfb_proc = None
            try:
                os.environ["DISPLAY"] = ":99"
                xvfb_proc = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x1024x24"])
                await asyncio.sleep(2)
                
                result = await self._run_logic(p, headless=False)
                return result
            except Exception as e:
                await self.notify(f"💥 Error Fatal: {str(e)}")
            finally:
                if xvfb_proc: xvfb_proc.terminate()
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    url = sys.argv[1]
    bot = DownloaderBot(url)
    asyncio.run(bot.run())
