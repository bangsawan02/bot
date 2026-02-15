import os
import asyncio
import subprocess
import requests
import re
import tempfile
import shutil
import math
import sys
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

# --- PLAYWRIGHT ASYNC & STEALTH ---
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        
        # State Playwright
        self.browser = None
        self.context = None

    async def _close_all(self):
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if os.path.exists(self.temp_download_dir):
            shutil.rmtree(self.temp_download_dir, ignore_errors=True)

    # =========================================================
    # --- 1. TELEGRAM HELPERS (Async Friendly) ---
    # =========================================================

    async def _notify(self, text):
        """Kirim atau edit pesan Telegram secara async."""
        print(f"[*] {text}")
        if not self.bot_token or not self.owner_id: return

        if self.initial_message_id is None:
            # Kirim pesan baru
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
            try:
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10).json())
                self.initial_message_id = res.get('result', {}).get('message_id')
            except: pass
        else:
            # Edit pesan
            url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
            payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, "text": text, "parse_mode": "Markdown"}
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10))
            except: pass

    # =========================================================
    # --- 2. EXTERNAL TOOLS (Aria2c & Megatools Async) ---
    # =========================================================

    async def _run_aria2c(self, urls, output_filename):
        await self._notify(f"⬇️ **Aria2c:** Memulai download `{output_filename}`")
        
        # Buat file input sementara untuk aria2c
        input_file = os.path.join(self.temp_download_dir, "aria_input.txt")
        with open(input_file, "w") as f:
            for u in urls: f.write(f"{u}\n")

        cmd = [
            'aria2c', '--allow-overwrite', '--file-allocation=none', 
            '-x', '16', '-s', '16', '-c', '--input-file', input_file, '-o', output_filename
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )

        # Monitor progress secara pasif (berdasarkan size file di disk)
        total_size = await self._get_size_remote(urls[0])
        last_percent = -1
        
        while process.returncode is None:
            if os.path.exists(output_filename):
                curr_size = os.path.getsize(output_filename)
                if total_size:
                    percent = int(curr_size * 100 // total_size)
                    if (percent % 25 == 0 or percent >= 99) and percent != last_percent:
                        await self._notify(f"⬇️ **Aria2c:** {percent}% dari {self._human_size(total_size)}")
                        last_percent = percent
                
                if total_size and curr_size >= total_size: break
            
            await asyncio.sleep(5)
            if process.returncode is not None: break

        await process.wait()
        return output_filename if os.path.exists(output_filename) else None

    async def _run_megatools(self, url):
        await self._notify("⬇️ **Megatools:** Menghubungkan ke MEGA...")
        
        # Megatools biasanya output ke stderr untuk progress
        process = await asyncio.create_subprocess_exec(
            'megatools', 'dl', url,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        # Megatools mendownload ke CWD, kita cari filenya
        # (Logika pencarian file bisa disesuaikan)
        return "Check_CWD_for_file" 

    # =========================================================
    # --- 3. BROWSER LOGIC (Playwright Async + Stealth) ---
    # =========================================================

    async def _handler_apkadmin(self, page):
        await self._notify("🔍 **ApkAdmin:** Menunggu link download...")
        final_url = None

        def catch_res(res):
            nonlocal final_url
            if ".apk" in res.url or ".zip" in res.url:
                if res.status == 200 and "apkadmin" not in res.url:
                    final_url = res.url

        page.on("response", catch_res)
        await page.goto(self.url)
        
        # Submit Form F1 (pemicu download)
        try:
            await page.locator("form[name='F1']").evaluate("f => f.submit()")
            # Tunggu interceptor bekerja
            for _ in range(15):
                if final_url: break
                await asyncio.sleep(1)
        except: pass
        
        if final_url:
            return await self._run_aria2c([final_url], "downloaded_file.apk")
        return None

    async def _handler_generic_browser(self, page):
        """Handler untuk Gofile, Mediafire, dll."""
        await page.goto(self.url)
        await self._notify("🔍 **Browser:** Mencari tombol download...")

        try:
            # Gunakan expect_download untuk menangkap stream file
            async with page.expect_download(timeout=60000) as download_info:
                # Coba klik tombol download yang umum
                selectors = ["#downloadButton", "#download-btn", "text='Download'", ".btn-download"]
                for s in selectors:
                    btn = page.locator(s).first
                    if await btn.is_visible():
                        await btn.click()
                        break
            
            download = await download_info.value
            path = os.path.join(os.getcwd(), download.suggested_filename)
            await download.save_as(path)
            await self._notify(f"✅ **Selesai:** `{download.suggested_filename}`")
            return download.suggested_filename
        except Exception as e:
            await self._notify(f"❌ Gagal di Browser: {str(e)[:50]}")
            return None

    # =========================================================
    # --- 4. MAIN ORCHESTRATOR ---
    # =========================================================

    async def run(self):
        await self._notify(f"⏳ **Analisis URL:** `{self.url}`")

        # 1. Jalankan Tool Tanpa Browser jika bisa
        if "mega.nz" in self.url:
            return await self._run_megatools(self.url)
        
        if "pixeldrain" in self.url:
            # Langsung API (Tanpa Browser)
            f_id = self.url.split('/')[-1]
            dl_url = f"https://pixeldrain.com/api/file/{f_id}?download"
            return await self._run_aria2c([dl_url], f"pixeldrain_{f_id}")

        # 2. Jalankan Playwright Stealth (Async Mode)
        async with Stealth().use_async(async_playwright()) as p:
            self.browser = await p.chromium.launch(headless=True)
            self.context = await self.browser.new_context(accept_downloads=True)
            page = await self.context.new_page()

            try:
                if "apkadmin" in self.url:
                    res = await self._handler_apkadmin(page)
                else:
                    res = await self._handler_generic_browser(page)
                return res
            finally:
                await self._close_all()

    # --- UTILS ---
    def _human_size(self, b):
        for unit in ['B','KB','MB','GB']:
            if b < 1024: return f"{b:.2f} {unit}"
            b /= 1024

    async def _get_size_remote(self, url):
        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: requests.head(url, allow_redirects=True, timeout=5))
            return int(r.headers.get('Content-Length', 0))
        except: return 0

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    bot = DownloaderBot(sys.argv[1])
    asyncio.run(bot.run())
