import os
import asyncio
import re
import sys
import subprocess
import requests
import tempfile
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from pyvirtualdisplay import Display

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # Gabungan selector bruteforce
        self.bruteforce_selectors = [
            "form[name='F1']", 
            "text='Download File'", 
            "#downloadButton", 
            ".downloadbtn",
            "button:has-text('Download')",
            "a:has-text('Download')",
            "#filemanager_itemslist button", 
            "a[href*='download']"
        ]
        self.combined_query = ", ".join(self.bruteforce_selectors)

    async def _notify(self, text):
        print(f"[*] {text}")

    # --- FAST PATH HANDLERS ---
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
        await self._notify("⬇️ Megatools: Downloading...")
        try:
            process = await asyncio.create_subprocess_exec(
                'megatools', 'dl', '--path', os.getcwd(), self.url,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            stdout, _ = await process.communicate()
            return "MEGA_SUCCESS" if process.returncode == 0 else None
        except Exception as e:
            await self._notify(f"🛑 Mega Error: {e}")
        return None

    async def _run_aria2c(self, url, filename, referer=None):
        cmd = ['aria2c', '--allow-overwrite=true', '--user-agent', self.user_agent, '-o', filename]
        if referer: cmd.extend(['--header', f'Referer: {referer}'])
        cmd.append(url)
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.wait()
        return filename if process.returncode == 0 else None

    # --- GENERIC BROWSER HANDLER ---
    async def _generic_browser_handler(self, page):
        for attempt in range(1, 4):
            await self._notify(f"🔎 Scanning (Attempt {attempt})...")
            try:
                await page.wait_for_selector(self.combined_query, state="attached", timeout=12000)
            except:
                await page.wait_for_load_state("networkidle", timeout=5000)

            # Identifikasi selector yang visible
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
                await self._notify(f"🎯 Found: `{found_sel}`. Clicking...")
                try:
                    async with page.expect_download(timeout=60000) as download_info:
                        if "form" in found_sel:
                            await target_el.evaluate("el => el.submit()")
                        else:
                            await target_el.click(force=True)
                    
                    download = await download_info.value
                    save_path = os.path.join(os.getcwd(), download.suggested_filename)
                    await download.save_as(save_path)
                    
                    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                        return save_path
                except Exception as e:
                    await self._notify(f"⚠️ Action failed: {str(e)[:50]}")
                    await page.wait_for_load_state("networkidle", timeout=5000)
        return None

    async def run(self):
        # Start Virtual Display (Xvfb)
        display = Display(visible=0, size=(1280, 720))
        display.start()
        await self._notify("🖥️ Xvfb Started (Headful mode simulation)")

        # Fast Path check
        if "pixeldrain.com" in self.url:
            res = await self._handle_pixeldrain()
            display.stop()
            return res
        if "mega.nz" in self.url:
            res = await self._handle_mega()
            display.stop()
            return res

        # Playwright dengan Stealth use_async & Headful
        async with Stealth().use_async(async_playwright()) as p:
            # Paksa headless=False karena pakai Xvfb
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Opening: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                # Auto-close popups
                page.on("popup", lambda p: p.close())

                result = await self._generic_browser_handler(page)
                if result:
                    await self._notify(f"✅ SUCCESS: {result}")
                else:
                    await self._notify("❌ FAILED: No file captured.")
                    sys.exit(1)
            finally:
                await browser.close()
                display.stop()
                await self._notify("🖥️ Xvfb Stopped")

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
