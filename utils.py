import os
import re
import asyncio
import subprocess
import shutil
import requests
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_async

class DownloaderBotAsync:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.initial_message_id = None

    # =========================================================
    # --- HELPERS (Telegram & System) ---
    # =========================================================

    async def _send_telegram(self, text):
        if not self.bot_token or not self.owner_id: return
        mode = "editMessageText" if self.initial_message_id else "sendMessage"
        api_url = f"https://api.telegram.org/bot{self.bot_token}/{mode}"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        if self.initial_message_id: payload["message_id"] = self.initial_message_id
        
        try:
            res = await asyncio.to_thread(requests.post, api_url, json=payload, timeout=10)
            data = res.json()
            if not self.initial_message_id:
                self.initial_message_id = data.get('result', {}).get('message_id')
        except: pass

    def _extract_filename(self, cd, url):
        if cd:
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m: return m.group(1)
        return os.path.basename(urlparse(url).path) or "downloaded_file"

    async def _download_aria2(self, url, name):
        if shutil.which("aria2c"):
            self._send_telegram(f"⚡ **Aria2c:** Meroketkan `{name}`...")
            cmd = ["aria2c", "-x", "16", "-s", "16", "-c", "-o", name, url]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await proc.wait()
            if proc.returncode == 0: return name
        return None

    # =========================================================
    # --- CORE LOGIC (Network Sniffer & Bruteforce) ---
    # =========================================================

    async def _bruteforce(self, page):
        sniffed = []
        
        # Sniffer: Tangkap link file yang lewat di jalan tol network
        async def on_response(response):
            try:
                ctype = (response.headers.get("content-type") or "").lower()
                cd = (response.headers.get("content-disposition") or "").lower()
                if any(x in ctype for x in ("application/", "octet-stream")) or "attachment" in cd:
                    if response.url not in sniffed:
                        sniffed.append(response.url)
            except: pass

        page.on("response", on_response)
        await page.goto(self.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 1. Cek apakah ada timer/countdown di halaman
        content = await page.content()
        if any(x in content.lower() for x in ("wait", "seconds", "readying")):
            await self._send_telegram("⏳ **Timer detected:** Menunggu 10 detik...")
            await page.wait_for_timeout(10000)

        # 2. Klik Bruteforce dengan Expect Download
        selectors = [
            "text=/.*[Dd]ownload.*/", 
            "button:has-text('Start')", 
            "#downloadbtn", 
            ".downloadbtn",
            "a[href*='download']"
        ]

        try:
            async with page.expect_download(timeout=60000) as download_info:
                for selector in selectors:
                    try:
                        btn = page.locator(selector).first
                        if await btn.is_visible():
                            # Klik paksa dengan JS biar gak ketutup overlay iklan
                            await btn.evaluate("el => el.click()")
                            await page.wait_for_timeout(2000)
                            if sniffed: break # Berhenti kalau sniffer dapet barang
                    except: continue
                
                download = await download_info.value
                fname = download.suggested_filename
                await self._send_telegram(f"✅ **Dapet!** Mengunduh: `{fname}`")
                await download.save_as(fname)
                return fname

        except PlaywrightTimeout:
            # Fallback ke hasil sniffed terakhir jika expect_download gagal
            if sniffed:
                target = sniffed[-1]
                fname = self._extract_filename(None, target)
                return await self._download_aria2(target, fname)
            raise Exception("Bruteforce gagal: tidak ada download event.")

    # =========================================================
    # --- ORCHESTRATOR WITH XVFB ---
    # =========================================================

    async def _run_with_xvfb(self, headless):
        """Menjalankan Playwright di dalam virtual display."""
        xvfb_proc = None
        if not headless:
            # Jalankan Xvfb secara manual jika di Linux
            display = ":99"
            os.environ["DISPLAY"] = display
            xvfb_proc = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x1024x24"])
            await asyncio.sleep(2) # Tunggu Xvfb siap

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            # Terapkan Stealth agar tidak terdeteksi sebagai bot
            await stealth_async(page)
            
            # Matikan popup iklan otomatis
            page.on("popup", lambda p: p.close())

            try:
                result = await self._bruteforce(page)
                return result
            finally:
                await browser.close()
                if xvfb_proc: xvfb_proc.terminate()

    async def run(self):
        await self._send_telegram(f"🚀 **Job Started:** `{self.url}`")
        try:
            # Coba headless pro dulu (lebih efisien)
            try:
                return await self._run_with_xvfb(headless=True)
            except Exception as e:
                await self._send_telegram(f"⚠️ Headless gagal, mencoba mode Xvfb Virtual...")
                return await self._run_with_xvfb(headless=False)
        except Exception as e:
            await self._send_telegram(f"💥 **Error:** `{str(e)}`")
            return None
