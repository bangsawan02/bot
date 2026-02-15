import os
import re
import sys
import asyncio
import tempfile
import mimetypes
import base64
import uuid
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

# -------------------------
# Helpers
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
    m2 = re.search(r'filename="?([^\";]+)"?', cd)
    return m2.group(1) if m2 else None

def guess_name_from_headers(headers: dict, url: str, suggested: str = None):
    cd = headers.get("content-disposition") if headers else None
    name = extract_filename_from_cd(cd) if cd else None
    if name:
        return sanitize_filename(name)
    if suggested:
        return sanitize_filename(suggested)
    base = os.path.basename(urlparse(url).path)
    if base:
        return sanitize_filename(base)
    ctype = (headers.get("content-type") or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(ctype) or ".bin"
    return sanitize_filename("downloaded_file" + ext)

# -------------------------
# Telegram helpers
# -------------------------
def _tg_send_message_sync(bot_token: str, chat_id: str, text: str):
    import requests
    api = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(api, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print("Telegram send message failed:", e)

def _tg_send_document_sync(bot_token: str, chat_id: str, file_path: str, caption: str = None):
    import requests
    api = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            requests.post(api, data=data, files=files, timeout=60)
    except Exception as e:
        print("Telegram send document failed:", e)

# -------------------------
# Robust click-and-capture helper
# -------------------------
async def click_and_capture(page, selector, timeout_download=30000):
    """
    Try multiple strategies to capture a download triggered by clicking selector:
    - expect_download on same page
    - detect popup/new page and expect_download there
    - sniff network responses for file-like responses and navigate to trigger download
    - handle blob: href by fetching blob and saving locally
    Returns saved filename or None.
    """
    def is_file_response(resp):
        try:
            url = (resp.url or "").lower()
            ctype = (resp.headers.get("content-type") or "").lower()
            if url.endswith((".apk", ".zip", ".exe", ".msi")):
                return True
            if "application/vnd.android.package-archive" in ctype or "application/octet-stream" in ctype:
                return True
        except Exception:
            pass
        return False

    locator = page.locator(selector).first
    if not (await locator.count()):
        return None

    try:
        await locator.scroll_into_view_if_needed()
    except:
        pass

    file_response = {"resp": None}
    async def on_response(resp):
        try:
            if is_file_response(resp):
                file_response["resp"] = resp
        except:
            pass
    page.on("response", on_response)

    # 1) expect_download on same page
    try:
        async with page.expect_download(timeout=timeout_download) as d:
            await locator.click()
        download = await d.value
        fname = download.suggested_filename or f"downloaded_{uuid.uuid4().hex}"
        await download.save_as(fname)
        page.off("response", on_response)
        return fname
    except Exception:
        pass

    # 2) click and wait for popup/new page
    try:
        await locator.click()
    except:
        pass

    try:
        new_page = await page.context.wait_for_event("page", timeout=3000)
    except:
        new_page = None

    if new_page:
        try:
            async with new_page.expect_download(timeout=timeout_download) as d2:
                # allow page to run scripts
                try:
                    await new_page.wait_for_load_state("domcontentloaded", timeout=3000)
                except:
                    pass
            download2 = await d2.value
            fname2 = download2.suggested_filename or f"downloaded_{uuid.uuid4().hex}"
            await download2.save_as(fname2)
            page.off("response", on_response)
            return fname2
        except Exception:
            # try sniffing responses on new_page
            try:
                resp = await new_page.wait_for_response(lambda r: is_file_response(r), timeout=5000)
                url = resp.url
                tmp = await page.context.new_page()
                try:
                    async with tmp.expect_download(timeout=15000) as d3:
                        await tmp.goto(url, wait_until="domcontentloaded")
                    dl3 = await d3.value
                    fn3 = dl3.suggested_filename or f"downloaded_{uuid.uuid4().hex}"
                    await dl3.save_as(fn3)
                    await tmp.close()
                    page.off("response", on_response)
                    return fn3
                except:
                    await tmp.close()
            except:
                pass

    # 3) sniffed response on original page
    if file_response.get("resp"):
        resp = file_response["resp"]
        url = resp.url
        try:
            tmp = await page.context.new_page()
            try:
                async with tmp.expect_download(timeout=15000) as d4:
                    await tmp.goto(url, wait_until="domcontentloaded")
                dl4 = await d4.value
                fn4 = dl4.suggested_filename or f"downloaded_{uuid.uuid4().hex}"
                await dl4.save_as(fn4)
                await tmp.close()
                page.off("response", on_response)
                return fn4
            except:
                await tmp.close()
        except:
            pass

    # 4) blob href handling
    try:
        href = await locator.get_attribute("href")
        if href and href.startswith("blob:"):
            b64 = await page.evaluate(
                """async (blobUrl) => {
                    const res = await fetch(blobUrl);
                    const buf = await res.arrayBuffer();
                    let binary = '';
                    const bytes = new Uint8Array(buf);
                    for (let i = 0; i < bytes.byteLength; i++) {
                        binary += String.fromCharCode(bytes[i]);
                    }
                    return btoa(binary);
                }""",
                href
            )
            data = base64.b64decode(b64)
            fname = f"downloaded_{uuid.uuid4().hex}.apk"
            with open(fname, "wb") as f:
                f.write(data)
            page.off("response", on_response)
            return fname
    except Exception:
        pass

    page.off("response", on_response)
    return None

# -------------------------
# Main downloader class
# -------------------------
class DownloaderBot:
    def __init__(self, url: str, max_attempts: int = 5):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("PAYLOAD_SENDER")
        self.max_attempts = max_attempts
        self.selectors = [
            "text=/.*Click here to download.*/i",
            "a[href$='.apk']",
            "a:has-text('Download')",
            "text=/.*Free Download.*/i",
            "text=/.*(Generate|Create|Get).*Link.*/i",
            "button:has-text('Download')",
            "a[class*='download']",
            "a[href*='download']"
        ]

    async def _send_telegram(self, text: str):
        if not self.bot_token or not self.chat_id:
            print("[TELEGRAM NOT CONFIGURED]", text)
            return
        await asyncio.to_thread(_tg_send_message_sync, self.bot_token, self.chat_id, text)

    async def _send_telegram_file(self, path: str, caption: str = None):
        if not self.bot_token or not self.chat_id:
            print("[TELEGRAM NOT CONFIGURED] send file:", path)
            return
        await asyncio.to_thread(_tg_send_document_sync, self.bot_token, self.chat_id, path, caption)

    async def _screenshot_temp(self, page):
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            await page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            try:
                os.remove(path)
            except:
                pass
            return None

    async def run(self):
        await self._send_telegram(f"🚀 Mulai download: `{self.url}`")
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent="Mozilla/5.0")
            page = await context.new_page()

            await page.goto(self.url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except:
                pass

            # initial wait: look for any selector within 10s
            found = None
            end = asyncio.get_event_loop().time() + 10
            while asyncio.get_event_loop().time() < end:
                for sel in self.selectors:
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() and await loc.is_visible():
                            found = sel
                            break
                    except:
                        continue
                if found:
                    break
                await asyncio.sleep(0.25)

            if not found:
                shot = await self._screenshot_temp(page)
                if shot:
                    await self._send_telegram("❌ Gagal: tidak ada selector muncul dalam 10 detik. Screenshot terlampir.")
                    await self._send_telegram_file(shot, caption="Screenshot gagal: tidak ada selector")
                    try:
                        os.remove(shot)
                    except:
                        pass
                else:
                    await self._send_telegram("❌ Gagal: tidak ada selector muncul dalam 10 detik.")
                await browser.close()
                return None

            await self._send_telegram(f"🔎 Selector ditemukan: `{found}` — mulai proses klik dan cek download")

            attempt = 0
            while attempt < self.max_attempts:
                attempt += 1
                try:
                    loc = page.locator(found).first
                    if (await loc.count()) and (await loc.is_visible()):
                        result = await click_and_capture(page, found, timeout_download=20000)
                        if result:
                            await self._send_telegram(f"✅ Download berhasil: `{result}`")
                            await self._send_telegram_file(result, caption=f"File: {result}")
                            await browser.close()
                            return result
                        # if not captured, click once more to trigger potential JS
                        try:
                            await loc.click()
                        except:
                            pass
                    else:
                        # if locator disappeared, try to find any selector quickly
                        new_found = None
                        end2 = asyncio.get_event_loop().time() + 3
                        while asyncio.get_event_loop().time() < end2 and not new_found:
                            for sel in self.selectors:
                                try:
                                    l2 = page.locator(sel).first
                                    if await l2.count() and await l2.is_visible():
                                        new_found = sel
                                        break
                                except:
                                    continue
                            await asyncio.sleep(0.2)
                        if new_found:
                            found = new_found
                            await self._send_telegram(f"🔁 Selector berganti ke `{found}` — lanjut")
                            continue
                except Exception:
                    pass

                # bruteforce scan all selectors once
                await self._send_telegram(f"🔁 Percobaan {attempt}: scan ulang selector")
                for sel in self.selectors:
                    try:
                        loc = page.locator(sel).first
                        if not (await loc.count()) or not (await loc.is_visible()):
                            continue
                        result = await click_and_capture(page, sel, timeout_download=15000)
                        if result:
                            await self._send_telegram(f"✅ Download berhasil: `{result}`")
                            await self._send_telegram_file(result, caption=f"File: {result}")
                            await browser.close()
                            return result
                        try:
                            await loc.click()
                        except:
                            pass
                        await page.wait_for_timeout(1000)
                    except Exception:
                        continue

                # reload and try again
                await self._send_telegram(f"🔁 Percobaan {attempt} gagal, reload halaman dan coba lagi")
                try:
                    await page.reload(wait_until="domcontentloaded")
                except:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except:
                    pass

                # find selector after reload
                new_found = None
                end3 = asyncio.get_event_loop().time() + 5
                while asyncio.get_event_loop().time() < end3 and not new_found:
                    for sel in self.selectors:
                        try:
                            l3 = page.locator(sel).first
                            if await l3.count() and await l3.is_visible():
                                new_found = sel
                                break
                        except:
                            continue
                    await asyncio.sleep(0.25)
                if new_found:
                    found = new_found
                    continue
                else:
                    shot = await self._screenshot_temp(page)
                    if shot:
                        await self._send_telegram("❌ Gagal: selector hilang setelah reload. Screenshot terlampir.")
                        await self._send_telegram_file(shot, caption="Screenshot gagal: selector hilang")
                        try:
                            os.remove(shot)
                        except:
                            pass
                    else:
                        await self._send_telegram("❌ Gagal: selector hilang setelah reload.")
                    await browser.close()
                    return None

            # reached max attempts without success
            shot = await self._screenshot_temp(page)
            if shot:
                await self._send_telegram("❌ Gagal: mencapai batas percobaan. Screenshot terlampir.")
                await self._send_telegram_file(shot, caption="Screenshot gagal: batas percobaan tercapai")
                try:
                    os.remove(shot)
                except:
                    pass
            else:
                await self._send_telegram("❌ Gagal: mencapai batas percobaan.")
            await browser.close()
            return None

# -------------------------
# CLI runner (example)
# -------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python utils.py <url>")
        sys.exit(1)
    url = sys.argv[1]
    bot = DownloaderBot(url, max_attempts=3)
    async def main():
        res = await bot.run()
        if res:
            print("Selesai:", res)
        else:
            print("Gagal.")
    asyncio.run(main())
