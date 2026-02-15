import os
import re
import sys
import asyncio
import tempfile
import mimetypes
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
# Telegram helpers (blocking requests run in thread)
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
# Main downloader class (uses Stealth wrapper)
# -------------------------
class DownloaderBot:
    def __init__(self, url: str, max_attempts: int = 5):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.chat_id = os.environ.get("PAYLOAD_SENDER")
        self.max_attempts = max_attempts

        # selector list to scan
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

    # wait up to timeout_ms for any selector in list to appear; return selector string or None
    async def _wait_for_any_selector(self, page, timeout_ms: int = 10000):
        end = asyncio.get_event_loop().time() + (timeout_ms / 1000)
        while asyncio.get_event_loop().time() < end:
            for sel in self.selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        return sel
                except Exception:
                    continue
            await asyncio.sleep(0.25)
        return None

    # click locator and capture Playwright default download
    async def _click_and_expect_download(self, page, locator, timeout_ms: int = 20000):
        try:
            async with page.expect_download(timeout=timeout_ms) as d:
                await locator.click()
            download = await d.value
            fname = download.suggested_filename or "downloaded_file"
            await download.save_as(fname)
            return fname
        except PlaywrightTimeout:
            return None
        except Exception:
            return None

    # scan all selectors once: click each and check download
    async def _bruteforce_once(self, page):
        for sel in self.selectors:
            try:
                loc = page.locator(sel).first
                if not (await loc.count()) or not (await loc.is_visible()):
                    continue
                # try expect_download first
                fname = await self._click_and_expect_download(page, loc, timeout_ms=15000)
                if fname:
                    return fname
                # if no download event, click and give a short moment
                try:
                    await loc.click()
                except:
                    pass
                await page.wait_for_timeout(1000)
                # short post-click download wait
                try:
                    async with page.expect_download(timeout=5000) as d2:
                        pass
                    download = await d2.value
                    fname2 = download.suggested_filename or "downloaded_file"
                    await download.save_as(fname2)
                    return fname2
                except:
                    pass
            except Exception:
                continue
        return None

    # take screenshot to temp file
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

    # main orchestration using Stealth wrapper
    async def run(self):
        await self._send_telegram(f"🚀 Mulai download: `{self.url}`")

        # Use Stealth wrapper so all contexts/pages have stealth applied
        async with Stealth().use_async(async_playwright()) as p:
            # p behaves like the async_playwright() object
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await context.new_page()

            # navigate and give JS some time to run
            await page.goto(self.url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except:
                pass

            # 1) wait up to 10s for any selector to appear
            found = await self._wait_for_any_selector(page, timeout_ms=10000)
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
                        fname = await self._click_and_expect_download(page, loc, timeout_ms=20000)
                        if fname:
                            await self._send_telegram(f"✅ Download berhasil: `{fname}`")
                            await self._send_telegram_file(fname, caption=f"File: {fname}")
                            await browser.close()
                            return fname
                        # if no download event, click and continue
                        try:
                            await loc.click()
                        except:
                            pass
                    else:
                        # if locator disappeared, try to find any selector quickly
                        new_found = await self._wait_for_any_selector(page, timeout_ms=3000)
                        if new_found:
                            found = new_found
                            await self._send_telegram(f"🔁 Selector berganti ke `{found}` — lanjut")
                            continue
                except Exception:
                    pass

                # bruteforce scan all selectors
                await self._send_telegram(f"🔁 Percobaan {attempt}: scan ulang selector")
                result = await self._bruteforce_once(page)
                if result:
                    await self._send_telegram(f"✅ Download berhasil: `{result}`")
                    await self._send_telegram_file(result, caption=f"File: {result}")
                    await browser.close()
                    return result

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
                new_found = await self._wait_for_any_selector(page, timeout_ms=5000)
                if new_found:
                    found = new_found
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
# CLI runner
# -------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <url>")
        sys.exit(1)
    url = sys.argv[1]
    bot = DownloaderBot(url, max_attempts=5)
    async def main():
        res = await bot.run()
        if res:
            print("Selesai:", res)
        else:
            print("Gagal.")
    asyncio.run(main())
