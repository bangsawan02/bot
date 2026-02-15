import os
import re
import sys
import asyncio
import uuid
import requests
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

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
# 📢 Telegram Helpers
# -------------------------
def _tg_send_message(bot_token, chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", 
                      json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def _tg_send_document(bot_token, chat_id, file_path, caption):
    try:
        if not os.path.exists(file_path): return
        with open(file_path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{bot_token}/sendDocument", 
                          data={"chat_id": chat_id, "caption": caption}, files={"document": f}, timeout=300)
    except: pass

# -------------------------
# 🎯 Core: Click and Capture
# -------------------------
async def click_and_capture(page, selector, timeout=60000):
    """
    Spesifik untuk Pixeldrain: Menunggu event download setelah klik.
    """
    try:
        # Menyiapkan listener download
        async with page.expect_download(timeout=timeout) as d_info:
            # Cari tombol, scroll, dan klik dengan delay human-like
            button = page.locator(selector).first
            await button.scroll_into_view_if_needed()
            await asyncio.sleep(1) 
            await button.click(force=True)
            
        download = await d_info.value
        # Simpan file
        fname = sanitize_filename(download.suggested_filename)
        await download.save_as(fname)
        return fname
    except Exception as e:
        print(f"[!] Gagal capture download: {e}")
        return None

# -------------------------
# 🤖 Main Downloader Class
# -------------------------
class DownloaderBot:
    def __init__(self, url: str):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("PAYLOAD_SENDER")
        # Selector berdasarkan outerHTML yang lu kasih
        self.selectors = [
            "button.button_highlight",      # Tombol utama di tengah
            "button.toolbar_button",        # Tombol di sidebar
            "text='Download'",              # Fallback teks
            "i:has-text('download') + span" # Target spesifik ke span 'Download'
        ]

    async def notify(self, text):
        print(f"[*] {text}")
        if self.bot_token and self.chat_id:
            await asyncio.to_thread(_tg_send_message, self.bot_token, self.chat_id, text)

    async def run(self):
        # Gunakan wrapper Stealth yang lu minta
        async with Stealth().use_async(async_playwright()) as p:
            # Pastikan headless=False agar tidak terdeteksi bot di Pixeldrain
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(viewport={'width': 1280, 'height': 1024})
            page = await context.new_page()
            
            await self.notify(f"🌐 Membuka URL: {self.url}")
            
            try:
                # Navigasi dengan timeout panjang (Pixeldrain kadang agak berat)
                await page.goto(self.url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(5) # Jeda untuk render Svelte

                # Cari tombol yang visible
                target = None
                for sel in self.selectors:
                    if await page.locator(sel).first.is_visible():
                        target = sel
                        break

                if target:
                    await self.notify(f"🎯 Klik tombol download...")
                    file_path = await click_and_capture(page, target)
                    
                    if file_path and os.path.exists(file_path):
                        await self.notify(f"✅ Download Sukses: `{file_path}`")
                        await asyncio.to_thread(_tg_send_document, self.bot_token, self.chat_id, file_path, f"Selesai: {file_path}")
                        # Hapus file setelah dikirim agar tidak memenuhi disk
                        os.remove(file_path)
                        return file_path
                
                await self.notify("❌ Tombol tidak ditemukan atau download gagal.")
                
            except Exception as e:
                await self.notify(f"💥 Terjadi kesalahan: {str(e)[:100]}")
            finally:
                await browser.close()
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    bot = DownloaderBot(sys.argv[1])
    asyncio.run(bot.run())
