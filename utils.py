import asyncio
import os
import re
import shutil
import subprocess
import requests
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

class DownloaderBotAsync:
    def __init__(self, url):
        self.url = url

    # -----------------------------
    # Helpers
    # -----------------------------
    def _tool(self, name):
        return shutil.which(name) is not None

    async def _download_requests(self, url, name):
        try:
            r = requests.get(url, stream=True, timeout=20)
            r.raise_for_status()
            with open(name, "wb") as f:
                for c in r.iter_content(8192):
                    if c: f.write(c)
            return name
        except Exception as e:
            print("requests fail:", e)
            return None

    async def _download_with_fallback(self, url, name):
        if self._tool("aria2c"):
            try:
                res = subprocess.run(
                    ["aria2c", "-x", "16", "-s", "16", "-c", "-o", name, url],
                    capture_output=True, text=True
                )
                if res.returncode == 0:
                    return name
            except Exception as e:
                print("aria2c fail:", e)
        return await self._download_requests(url, name)

    def _head_is_file(self, url):
        try:
            h = requests.head(url, allow_redirects=True, timeout=6)
            ctype = (h.headers.get("content-type") or "").lower()
            cd = h.headers.get("content-disposition", "")
            if cd or any(x in ctype for x in ("application/", "octet-stream", "zip", "apk")):
                return True, cd
        except Exception as e:
            print("HEAD fail:", e)
        return False, None

    async def _extract_filename(self, cd, url):
        if cd:
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m: return m.group(1)
        return os.path.basename(urlparse(url).path) or "downloaded_file"

    # -----------------------------
    # Bruteforce Async
    # -----------------------------
    async def _bruteforce(self, page):
        sniffed = []

        async def on_resp(resp):
            try:
                headers = resp.headers
                ctype = headers.get("content-type", "").lower()
                cd = headers.get("content-disposition", "").lower()
                if "application/" in ctype or "octet-stream" in ctype or "attachment" in cd:
                    sniffed.append(resp.url)
            except: pass

        page.on("response", on_resp)
        await page.goto(self.url, wait_until="domcontentloaded")

        # scrape anchors
        for a in await page.locator("a").all():
            href = await a.get_attribute("href")
            if href:
                full_url = urljoin(self.url, href)
                if any(ext in full_url.lower() for ext in ['.apk','.zip','.rar','.exe','.7z']):
                    ok, cd = await asyncio.to_thread(self._head_is_file, full_url)
                    if ok:
                        name = await self._extract_filename(cd, full_url)
                        return await self._download_with_fallback(full_url, name)

        # bruteforce click + expect_download
        selectors = ["text=/.*[Dd]ownload.*/", ".downloadbtn", "#downloadbtn", "text='Start Download'"]
        try:
            async with page.expect_download(timeout=15000) as dl_info:
                for sel in selectors:
                    btn = page.locator(sel).first
                    if await btn.is_visible():
                        await btn.click(no_wait_after=True)
                        await page.wait_for_timeout(2000)
                        if sniffed:
                            target = sniffed[-1]
                            ok, cd = await asyncio.to_thread(self._head_is_file, target)
                            if ok:
                                name = await self._extract_filename(cd, target)
                                return await self._download_with_fallback(target, name)

            download = await dl_info.value
            fname = download.suggested_filename
            await download.save_as(fname)
            return fname
        except PlaywrightTimeout:
            raise Exception("Bruteforce gagal: tidak ada download event.")

    # -----------------------------
    # Orchestrator (auto headless→headful)
    # -----------------------------
    async def _run_session(self, headless):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            ctx = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await ctx.new_page()
            try:
                return await self._bruteforce(page)
            finally:
                await browser.close()

    async def run(self):
        # coba headless dulu
        try:
            return await self._run_session(headless=True)
        except Exception as e:
            print("Headless gagal, switching to headful:", e)
        # fallback ke headful
        return await self._run_session(headless=False)


# --- Cara pakai ---
# asyncio.run(DownloaderBotAsync("https://apkadmin.com/...").run())
