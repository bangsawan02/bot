import os
import asyncio
import re
import sys
import requests
import tempfile
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # List Selector Bruteforce untuk situs-situs umum
        self.bruteforce_selectors = [
            "form[name='F1']", 
            "text='Download File'", 
            "#downloadButton", 
            ".downloadbtn",
            "button:has-text('Download')",
            "a:has-text('Download')",
            "#filemanager_itemslist > div:nth-child(7) > div > div:nth-child(2) > div > button", # Gofile
            ".btn-success",
            ".btn-primary",
            "#direct-download" # SourceForge manual
        ]
        self.combined_query = ", ".join(self.bruteforce_selectors)

    async def _notify(self, text):
        print(f"[*] {text}")

    # =========================================================
    # --- HANDLER 1: NON-BROWSER (API / TOOLS) ---
    # =========================================================

    async def _handle_pixeldrain(self):
        match = re.search(r"/(?:u|file)/([a-zA-Z0-9]+)", self.url)
        if not match: return None
        f_id = match.group(1)
        api_url = f"https://pixeldrain.com/api/file/{f_id}/info"
        
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: requests.get(api_url).json())
            if info.get("success"):
                filename = info.get("name")
                dl_url = f"https://pixeldrain.com/api/file/{f_id}?download"
                await self._notify(f"📦 Pixeldrain Detected: {filename}")
                return await self._run_aria2c(dl_url, filename, "https://pixeldrain.com/")
        except: pass
        return None

    async def _handle_mega(self):
        await self._notify("⬇️ Megatools: Memulai proses download MEGA...")
        process = await asyncio.create_subprocess_exec(
            'megatools', 'dl', self.url,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        await process.wait()
        return "MEGA_FILE" if process.returncode == 0 else None

    async def _run_aria2c(self, url, filename, referer=None):
        """Helper untuk download file direct dengan Aria2c."""
        cmd = ['aria2c', '--allow-overwrite=true', '--user-agent', self.user_agent, '-o', filename]
        if referer: cmd.extend(['--header', f'Referer: {referer}'])
        cmd.append(url)
        
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.wait()
        return filename if process.returncode == 0 else None

    # =========================================================
    # --- HANDLER 2: BROWSER BRUTEFORCE (STEALTH) ---
    # =========================================================

    async def _generic_browser_handler(self, page, context):
        for attempt in range(1, 4):
            await self._notify(f"🔎 Memindai halaman (Percobaan {attempt})...")
            try:
                await page.wait_for_selector(self.combined_query, state="attached", timeout=15000)
            except:
                await page.wait_for_load_state("networkidle", timeout=5000)

            target_el = None
            found_sel = None
            for sel in self.bruteforce_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        target_el = el
                        found_sel = sel
                        break
                except: continue

            if target_el:
                await self._notify(f"🎯 Target: `{found_sel}`")
                try:
                    async with page.expect_download(timeout=60000) as download_info:
                        if "form" in found_sel:
                            await page.evaluate(f"document.querySelector('{found_sel}').submit()")
                        else:
                            await target_el.click()
                    
                    download = await download_info.value
                    save_path = os.path.join(os.getcwd(), download.suggested_filename)
                    await download.save_as(save_path)
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                        return save_path
                except:
                    await page.wait_for_load_state("networkidle", timeout=10000)
        return None

    # =========================================================
    # --- MAIN ORCHESTRATOR ---
    # =========================================================

    async def run(self):
        # 1. Cek Source Cepat (Tanpa Browser)
        if "pixeldrain.com" in self.url:
            res = await self._handle_pixeldrain()
            if res: return res

        if "mega.nz" in self.url:
            res = await self._handle_mega()
            if res: return res

        # 2. Cek Source Umum dengan Playwright Stealth
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                # Sourceforge & Gofile butuh networkidle agar link download muncul
                await page.goto(self.url, wait_until="networkidle", timeout=60000)
                
                # Jalankan Bruteforce
                result = await self._generic_browser_handler(page, context)
                
                if result:
                    await self._notify(f"✅ SUKSES: {result}")
                else:
                    await self._notify("❌ GAGAL: File tidak ditemukan.")
                    sys.exit(1)
            finally:
                await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
