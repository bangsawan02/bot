import os
import asyncio
import re
import sys
import subprocess
import shutil
import requests
import tempfile
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # Selector lebih spesifik untuk menghindari "false positive"
        self.bruteforce_selectors = [
            "form[name='F1']", 
            "a:has-text('Download File')", 
            "button#downloadButton", 
            "a.downloadbtn",
            "button:has-text('Download')",
            "a:has-text('Download')",
            "#filemanager_itemslist button", 
            "a[href*='download']",
            "a[href*='sourceforge.net/projects/']"
        ]
        self.combined_query = ", ".join(self.bruteforce_selectors)

    async def _notify(self, text):
        print(f"[*] {text}")

    # =========================================================
    # --- HANDLER 1: FAST-PATH (API / TOOLS) ---
    # =========================================================

    async def _handle_pixeldrain(self):
        match = re.search(r"/(?:u|file)/([a-zA-Z0-9]+)", self.url)
        if not match: return None
        f_id = match.group(1)
        api_url = f"https://pixeldrain.com/api/file/{f_id}/info"
        
        try:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(None, lambda: requests.get(api_url, timeout=10))
            info = res.json()
            if info.get("success"):
                filename = info.get("name")
                dl_url = f"https://pixeldrain.com/api/file/{f_id}?download"
                await self._notify(f"📦 Pixeldrain: {filename}")
                return await self._run_aria2c(dl_url, filename, "https://pixeldrain.com/")
        except Exception as e:
            await self._notify(f"⚠️ Pixeldrain API Error: {e}")
        return None

    async def _handle_mega(self):
        await self._notify("⬇️ Megatools: Memulai...")
        try:
            # Pake shell=False untuk keamanan
            process = await asyncio.create_subprocess_exec(
                'megatools', 'dl', '--path', os.getcwd(), self.url,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                return "MEGA_DOWNLOAD_SUCCESS"
            await self._notify(f"❌ Mega Error: {stdout.decode()[:100]}")
        except Exception as e:
            await self._notify(f"🛑 Mega Execution Error: {e}")
        return None

    async def _run_aria2c(self, url, filename, referer=None):
        cmd = ['aria2c', '--allow-overwrite=true', '--user-agent', self.user_agent, '-o', filename]
        if referer: cmd.extend(['--header', f'Referer: {referer}'])
        cmd.append(url)
        
        try:
            process = await asyncio.create_subprocess_exec(*cmd)
            await process.wait()
            return filename if process.returncode == 0 else None
        except Exception as e:
            await self._notify(f"🛑 Aria2c Error: {e}")
        return None

    # =========================================================
    # --- HANDLER 2: STEALTH BROWSER (GENERIC) ---
    # =========================================================

    async def _generic_browser_handler(self, page, context):
        for attempt in range(1, 4):
            await self._notify(f"🔎 Scanning (Attempt {attempt})...")
            try:
                # Tunggu selector utama muncul
                await page.wait_for_selector(self.combined_query, state="attached", timeout=10000)
            except PlaywrightTimeout:
                await self._notify("⏳ Timeout waiting for selectors. Trying networkidle...")
                await page.wait_for_load_state("networkidle", timeout=5000)

            target_el = None
            found_sel = None
            
            # Cari element yang beneran visible
            for sel in self.bruteforce_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        target_el = el
                        found_sel = sel
                        break
                except: continue

            if target_el:
                await self._notify(f"🎯 Found: `{found_sel}`. Clicking...")
                try:
                    async with page.expect_download(timeout=60000) as download_info:
                        if "form" in found_sel:
                            # Cara aman eksekusi submit tanpa gangguan quote
                            await target_el.evaluate("el => el.submit()")
                        else:
                            # Klik dengan force=True kalau ada overlay transparan
                            await target_el.click(force=True, timeout=5000)
                    
                    download = await download_info.value
                    save_path = os.path.join(os.getcwd(), download.suggested_filename)
                    await download.save_as(save_path)
                    
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                        return save_path
                except Exception as e:
                    await self._notify(f"⚠️ Click/Download failed: {str(e)[:100]}")
                    await page.wait_for_load_state("networkidle", timeout=5000)
        return None

    # =========================================================
    # --- ORCHESTRATOR ---
    # =========================================================

    async def run(self):
        # 1. Fast Path
        if "pixeldrain.com" in self.url:
            return await self._handle_pixeldrain()
        if "mega.nz" in self.url:
            return await self._handle_mega()

        # 2. Browser Path (Stealth correctly implemented)
        async with async_playwright() as p:
            # CI/Docker friendly args
            browser = await p.chromium.launch(
                headless=True, 
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(
                user_agent=self.user_agent, 
                accept_downloads=True,
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()
            
            # Terapkan Stealth ke PAGE, bukan ke playwright object
            await Stealth().apply_async(page)

            try:
                await self._notify(f"🌐 Opening: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                # Tutup popup tak diundang
                page.on("popup", lambda p: p.close())

                result = await self._generic_browser_handler(page, context)
                
                if result:
                    await self._notify(f"✅ SUCCESS: {result}")
                else:
                    await self._notify("❌ FAILED: No file captured.")
                    sys.exit(1)
            except Exception as e:
                await self._notify(f"🛑 Critical Browser Error: {e}")
                sys.exit(1)
            finally:
                await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <URL>")
        sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
