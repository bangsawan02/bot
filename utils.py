import os
import re
import sys
import asyncio
import subprocess
import shutil
import requests
import mimetypes
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_async

# -------------------------
# Utility helpers
# -------------------------
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    return name[:200] if name else "downloaded_file"

def extract_filename_from_cd(cd: str):
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None

def guess_name_from_headers_or_url(headers: dict, url: str, suggested: str = None):
    cd = headers.get("content-disposition") if headers else None
    name = extract_filename_from_cd(cd) if cd else None
    if name:
        return sanitize_filename(name)
    if suggested:
        return sanitize_filename(suggested)
    path = urlparse(url).path
    base = os.path.basename(path)
    if base:
        return sanitize_filename(base)
    ctype = (headers.get("content-type") or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(ctype) or ".bin"
    return sanitize_filename("downloaded_file" + ext)

# -------------------------
# DownloaderBotAsync
# -------------------------
class DownloaderBotAsync:
    def __init__(self, url: str):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.initial_message_id = None

    # Telegram helper (best-effort)
    async def _send_telegram(self, text: str):
        if not self.bot_token or not self.owner_id:
            print("[TELEGRAM NOT CONFIGURED]", text)
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

    # aria2c or requests fallback
    async def _download_aria2(self, url: str, name: str):
        # Try aria2c
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
                cd = r.headers.get("content-disposition")
                fname = guess_name_from_headers_or_url(r.headers, r.url, suggested=name)
                with open(fname, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                if os.path.exists(fname):
                    return fname
        except Exception as e:
            print("requests download failed:", e)
        return None

    # Core bruteforce + sniffer
    async def _bruteforce(self, page):
        sniffed = []
        last_response_headers = {}

        async def on_response(response):
            try:
                url = response.url
                headers = {k.lower(): v for k, v in response.headers.items()}
                ctype = (headers.get("content-type") or "").lower()
                cd = (headers.get("content-disposition") or "").lower()
                last_response_headers[url] = headers
                if any(x in ctype for x in ("application/", "octet-stream")) or "attachment" in cd or url.lower().endswith((".apk", ".zip", ".exe", ".msi", ".tar.gz", ".apk.html")):
                    if url not in sniffed:
                        sniffed.append(url)
            except Exception:
                pass

        page.on("response", on_response)

        await page.goto(self.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        content = await page.content()
        if any(x in content.lower() for x in ("wait", "seconds", "readying", "please wait")):
            await self._send_telegram("⏳ **Timer detected:** Menunggu 10 detik...")
            await page.wait_for_timeout(10000)

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

        async def try_click(locator):
            try:
                await locator.scroll_into_view_if_needed()
            except:
                pass
            try:
                await locator.click(timeout=3000)
                return True
            except:
                pass
            try:
                await locator.evaluate("el => el.click()")
                return True
            except:
                pass
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

        try:
            async with page.expect_download(timeout=60000) as download_info:
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
                                for _ in range(6):
                                    await page.wait_for_timeout(1000)
                                    if sniffed:
                                        break
                                if sniffed:
                                    break
                    except Exception:
                        continue

                if not sniffed:
                    anchors = await page.query_selector_all("a")
                    for a in anchors:
                        try:
                            href = await a.get_attribute("href")
                            if href and any(ext in href.lower() for ext in [".apk", ".zip", ".exe", ".msi"]):
                                await self._send_telegram(f"🔗 Anchor direct link found: `{href}`")
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
            await self._send_telegram(f"⚠️ Expect download timeout. Sniffed count: {len(sniffed)}")
            print("Sniffed URLs:", sniffed)
            for u in sniffed:
                print(" -", u, last_response_headers.get(u))
            if sniffed:
                target = sniffed[-1]
                headers = last_response_headers.get(target, {})
                fname = guess_name_from_headers_or_url(headers, target)
                result = await self._download_aria2(target, fname)
                if result:
                    return result
                try:
                    head = requests.head(target, allow_redirects=True, timeout=10)
                    cd2 = head.headers.get("content-disposition")
                    fname2 = guess_name_from_headers_or_url(head.headers, head.url, suggested=fname)
                    result2 = await self._download_aria2(head.url, fname2)
                    if result2:
                        return result2
                except Exception as e:
                    print("HEAD fallback failed:", e)
            raise Exception("Bruteforce gagal: tidak ada download event dan fallback gagal.")

    # Run with optional Xvfb
    async def _run_with_xvfb(self, headless: bool):
        xvfb_proc = None
        if not headless:
            display = ":99"
            os.environ["DISPLAY"] = display
            try:
                xvfb_proc = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x1024x24"])
                await asyncio.sleep(2)
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

            # apply stealth
            try:
                await stealth_async(page)
            except Exception as e:
                await self._send_telegram(f"stealth apply failed: {e}")
                print("stealth apply failed:", e)

            # close popups safely
            page.on("popup", lambda popup: asyncio.create_task(popup.close()))

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
            try:
                return await self._run_with_xvfb(headless=True)
            except Exception as e:
                await self._send_telegram(f"⚠️ Headless failed: `{str(e)}`. Trying Xvfb mode...")
                return await self._run_with_xvfb(headless=False)
        except Exception as e:
            await self._send_telegram(f"💥 **Error:** `{str(e)}`")
            print("Final error:", e)
            return None
