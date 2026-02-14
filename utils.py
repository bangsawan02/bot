import os
import re
import asyncio
import subprocess
import shutil
import requests
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth

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
        if not self.bot_token or not self.owner_id:
            print("Telegram not configured:", text)
            return
        mode = "editMessageText" if self.initial_message_id else "sendMessage"
        api_url = f"https://api.telegram.org/bot{self.bot_token}/{mode}"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        if self.initial_message_id:
            payload["message_id"] = self.initial_message_id

        try:
            res = await asyncio.to_thread(requests.post, api_url, json=payload, timeout=10)
            data = res.json()
            if not self.initial_message_id:
                self.initial_message_id = data.get('result', {}).get('message_id')
        except Exception as e:
            print("Telegram send failed:", e)

    def _extract_filename(self, cd, url):
        if cd:
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m:
                return m.group(1)
        path = urlparse(url).path
        name = os.path.basename(path)
        return name or "downloaded_file"

    async def _download_aria2(self, url, name):
        # Try aria2c if available
        if shutil.which("aria2c"):
            await self._send_telegram(f"⚡ **Aria2c:** Meroketkan `{name}`...")
            cmd = ["aria2c", "-x", "16", "-s", "16", "-c", "-o", name, url]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(name):
                return name
            else:
                print("aria2c failed:", proc.returncode, stderr.decode(errors="ignore"))
        # Fallback to requests streaming
        try:
            await self._send_telegram(f"⬇️ **Requests fallback:** Mengunduh `{name}`...")
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                # try to get filename from headers
                cd = r.headers.get("content-disposition")
                fname = self._extract_filename(cd, url) or name
                with open(fname, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                if os.path.exists(fname):
                    return fname
        except Exception as e:
            print("requests download failed:", e)
        return None

    # =========================================================
    # --- CORE LOGIC (Network Sniffer & Bruteforce) ---
    # =========================================================

    async def _bruteforce(self, page):
        sniffed = []
        last_response_headers = {}

        # Sniffer: capture file-like responses
        async def on_response(response):
            try:
                url = response.url
                headers = {k.lower(): v for k, v in response.headers.items()}
                ctype = (headers.get("content-type") or "").lower()
                cd = (headers.get("content-disposition") or "").lower()
                # store last headers for fallback naming
                last_response_headers[url] = headers
                if any(x in ctype for x in ("application/", "octet-stream")) or "attachment" in cd or url.endswith((".apk", ".zip", ".exe", ".msi", ".tar.gz")):
                    if url not in sniffed:
                        sniffed.append(url)
            except Exception:
                pass

        page.on("response", on_response)
        await page.goto(self.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # 1. Cek apakah ada timer/countdown di halaman
        content = await page.content()
        if any(x in content.lower() for x in ("wait", "seconds", "readying", "please wait")):
            await self._send_telegram("⏳ **Timer detected:** Menunggu 10 detik...")
            await page.wait_for_timeout(10000)

        # 2. Klik Bruteforce dengan Expect Download
        selectors = [
            "text=/.*[Dd]ownload.*/",
            "button:has-text('Start')",
            "#downloadbtn",
            ".downloadbtn",
            "a[href*='download']",
            "a:has-text('Download')",
            "a:has-text('Get APK')",
            "button:has-text('Download')",
            "a[class*='download']",
            "a[href$='.apk']"
        ]

        # helper to try multiple click methods
        async def try_click(locator):
            try:
                await locator.scroll_into_view_if_needed()
            except: pass
            # try normal click
            try:
                await locator.click(timeout=3000)
                return True
            except:
                pass
            # try JS click
            try:
                await locator.evaluate("el => el.click()")
                return True
            except:
                pass
            # try dispatch event
            try:
                await locator.evaluate("""
                    el => {
                        const ev = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
                        el.dispatchEvent(ev);
                    }
                """)
                return True
            except:
                pass
            return False

        # Start waiting for download event but also attempt clicks and monitor sniffed
        try:
            async with page.expect_download(timeout=60000) as download_info:
                # try each selector multiple times
                for selector in selectors:
                    try:
                        loc = page.locator(selector).first
                        visible = False
                        try:
                            visible = await loc.is_visible()
                        except:
                            visible = False
                        if visible:
                            await self._send_telegram(f"🔎 Menemukan selector: `{selector}` — mencoba klik...")
                            clicked = await try_click(loc)
                            if clicked:
                                # give some time for network events
                                for _ in range(6):
                                    await page.wait_for_timeout(1000)
                                    if sniffed:
                                        break
                                if sniffed:
                                    break
                    except Exception:
                        continue

                # If no visible selector found, try clicking anchors by scanning all anchors
                if not sniffed:
                    anchors = await page.query_selector_all("a")
                    for a in anchors:
                        try:
                            href = await a.get_attribute("href")
                            if href and any(ext in href for ext in [".apk", ".zip", ".exe", ".msi"]):
                                await self._send_telegram(f"🔗 Anchor direct link found: `{href}`")
                                # try to navigate directly to href to trigger download
                                await page.goto(urljoin(self.url, href), wait_until="domcontentloaded")
                                await page.wait_for_timeout(2000)
                                if sniffed:
                                    break
                        except:
                            continue

                download = await download_info.value
                fname = download.suggested_filename or "downloaded_file"
                await self._send_telegram(f"✅ **Dapet!** Mengunduh: `{fname}`")
                await download.save_as(fname)
                return fname

        except PlaywrightTimeout:
            # Debug: report sniffed list periodically
            await self._send_telegram(f"⚠️ Expect download timeout. Sniffed count: {len(sniffed)}")
            # If sniffed URLs exist, try last one
            if sniffed:
                target = sniffed[-1]
                headers = last_response_headers.get(target, {})
                cd = headers.get("content-disposition")
                fname = self._extract_filename(cd, target)
                result = await self._download_aria2(target, fname)
                if result:
                    return result
                # if aria2 and requests failed, try HEAD to resolve redirect
                try:
                    head = requests.head(target, allow_redirects=True, timeout=10)
                    cd2 = head.headers.get("content-disposition")
                    fname2 = self._extract_filename(cd2, head.url)
                    result2 = await self._download_aria2(head.url, fname2)
                    if result2:
                        return result2
                except Exception:
                    pass
            # nothing worked
            raise Exception("Bruteforce gagal: tidak ada download event dan fallback gagal.")

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
            try:
                xvfb_proc = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x1024x24"])
                await asyncio.sleep(2)  # Tunggu Xvfb siap
            except Exception as e:
                print("Xvfb start failed:", e)

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
            try:
                # playwright_stealth exports stealth function
                await stealth(page)
            except Exception as e:
                print("stealth apply failed:", e)

            # Matikan popup iklan otomatis
            page.on("popup", lambda p: asyncio.create_task(p.close()))

            try:
                result = await self._bruteforce(page)
                return result
            finally:
                try:
                    await browser.close()
                except:
                    pass
                if xvfb_proc:
                    try:
                        xvfb_proc.terminate()
                    except:
                        pass

    async def run(self):
        await self._send_telegram(f"🚀 **Job Started:** `{self.url}`")
        try:
            # Coba headless dulu
            try:
                return await self._run_with_xvfb(headless=True)
            except Exception as e:
                await self._send_telegram(f"⚠️ Headless gagal: `{str(e)}`. Mencoba mode Xvfb Virtual...")
                return await self._run_with_xvfb(headless=False)
        except Exception as e:
            await self._send_telegram(f"💥 **Error:** `{str(e)}`")
            return None
