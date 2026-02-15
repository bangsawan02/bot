import os
import re
import sys
import asyncio
import mimetypes
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# -------------------------
# Robust stealth import
# -------------------------
stealth_async = None
try:
    from playwright_stealth.stealth import stealth_async
except Exception:
    try:
        from playwright_stealth import stealth_async
    except Exception:
        stealth_async = None

async def apply_stealth(page):
    if not stealth_async:
        return
    try:
        await stealth_async(page)
    except Exception as e:
        print("Stealth apply failed:", e)

# -------------------------
# Filename helpers
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
# DownloaderBotAsync
# -------------------------
class DownloaderBotAsync:
    def __init__(self, url: str, max_attempts: int = 3):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.initial_message_id = None
        self.max_attempts = max_attempts

    # Telegram helper (best-effort)
    async def _send_telegram(self, text: str):
        if not self.bot_token or not self.owner_id:
            print("[TELEGRAM NOT CONFIGURED]", text)
            return
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        try:
            await asyncio.to_thread(__import__("requests").post, api_url, json=payload, timeout=10)
        except Exception as e:
            print("Telegram send failed:", e)

    # detect countdown/timer on page; return seconds to wait (0 if none)
    async def _detect_countdown_seconds(self, page):
        # try common DOM patterns and inline scripts
        try:
            # 1) visible countdown element (#countdown, .countdown, .timer)
            js_check = """
            () => {
                const sel = document.querySelector('#countdown, .countdown, .timer, [data-countdown], [data-seconds], [data-timer]');
                if (sel) {
                    const txt = (sel.innerText || sel.textContent || '').trim();
                    const m = txt.match(/(\\d{1,3})/);
                    if (m) return parseInt(m[1], 10);
                    const v = sel.getAttribute('data-countdown') || sel.getAttribute('data-seconds') || sel.getAttribute('data-timer');
                    if (v) return parseInt(v,10) || 0;
                }
                // meta refresh
                const meta = document.querySelector('meta[http-equiv=refresh]');
                if (meta) {
                    const c = meta.getAttribute('content') || '';
                    const m2 = c.match(/(\\d+)/);
                    if (m2) return parseInt(m2[1],10);
                }
                // inline script setTimeout detection (ms)
                const scripts = Array.from(document.scripts).map(s => s.textContent || '').join('\\n');
                const m3 = scripts.match(/setTimeout\\s*\\([^,]+,\\s*(\\d{3,6})\\s*\\)/);
                if (m3) return Math.ceil(parseInt(m3[1],10)/1000);
                return 0;
            }
            """
            val = await page.evaluate(js_check)
            try:
                secs = int(val or 0)
            except:
                secs = 0
            return max(0, secs)
        except Exception:
            return 0

    # wait for selector from list to appear within timeout seconds; return the selector found or None
    async def _wait_for_any_selector(self, page, selectors, timeout=10000):
        # try waiting for any selector by polling each with small timeout slices
        end = asyncio.get_event_loop().time() + (timeout / 1000)
        while asyncio.get_event_loop().time() < end:
            for sel in selectors:
                try:
                    handle = page.locator(sel).first
                    if await handle.count() and await handle.is_visible():
                        return sel
                except Exception:
                    continue
            await asyncio.sleep(0.25)
        return None

    # try clicking a locator and use Playwright default download capture
    async def _click_and_capture_download(self, page, locator, timeout=20000):
        try:
            async with page.expect_download(timeout=timeout) as d:
                await locator.click()
            download = await d.value
            fname = download.suggested_filename or "downloaded_file"
            await download.save_as(fname)
            return fname
        except PlaywrightTimeout:
            return None
        except Exception:
            return None

    # sniff network responses for file-like URLs (simple, short window)
    async def _sniff_for_file(self, page, wait_ms=2500):
        sniffed = []
        last_headers = {}

        async def on_response(response):
            try:
                url = response.url
                if url.endswith(".js"):
                    return
                headers = {k.lower(): v for k, v in response.headers.items()}
                last_headers[url] = headers
                ctype = (headers.get("content-type") or "").lower()
                cd = (headers.get("content-disposition") or "").lower()
                if ("application/" in ctype or "octet-stream" in ctype or "attachment" in cd or url.lower().endswith((".apk", ".zip", ".exe", ".msi"))):
                    if url not in sniffed:
                        sniffed.append(url)
            except Exception:
                pass

        page.on("response", on_response)
        await page.wait_for_timeout(wait_ms)
        if sniffed:
            target = sniffed[-1]
            headers = last_headers.get(target, {})
            suggested = guess_name_from_headers(headers, target)
            # use Playwright default download by navigating to the URL in same context
            try:
                # open a new page to trigger browser download
                newp = await page.context.new_page()
                await newp.goto(target, wait_until="domcontentloaded")
                # try to capture download event if server triggers it
                try:
                    async with newp.expect_download(timeout=10000) as d:
                        # attempt to click any direct anchor if present
                        anchors = newp.locator("a[href]").all()
                        # if no download event, just wait a bit
                        pass
                    download = await d.value
                    fname = download.suggested_filename or suggested
                    await download.save_as(fname)
                    await newp.close()
                    return fname
                except Exception:
                    # fallback: return target URL so caller can handle
                    await newp.close()
                    return await self._download_via_navigate(target, suggested)
            except Exception:
                return await self._download_via_navigate(target, suggested)
        return None

    # simple navigate-and-save using browser download (default) or fallback to direct GET if needed
    async def _download_via_navigate(self, url, suggested):
        # try to use Playwright's default by opening a new browser context/page
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context(user_agent="Mozilla/5.0")
                page = await context.new_page()
                try:
                    async with page.expect_download(timeout=15000) as d:
                        await page.goto(url, wait_until="domcontentloaded")
                    download = await d.value
                    fname = download.suggested_filename or suggested or "downloaded_file"
                    await download.save_as(fname)
                    await browser.close()
                    return fname
                except Exception:
                    await browser.close()
        except Exception:
            pass
        # last resort: use requests (synchronous) to fetch
        try:
            import requests
            r = requests.get(url, stream=True, timeout=30)
            r.raise_for_status()
            fname = guess_name_from_headers(r.headers, r.url, suggested)
            with open(fname, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            return fname
        except Exception:
            return None

    # main orchestration per user's spec
    async def _run_browser(self):
        selectors = [
            "text=/.*Click here to download.*/i",
            "a[href$='.apk']",
            "a:has-text('Download')",
            "text=/.*Free Download.*/i",
            "text=/.*(Generate|Create|Get).*Link.*/i",
            "button:has-text('Download')",
            "a[class*='download']",
            "a[href*='download']"
        ]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await context.new_page()

            await apply_stealth(page)

            await page.goto(self.url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except:
                pass

            # 1) Wait up to 10s for any selector from bruteforce list to appear
            found_sel = await self._wait_for_any_selector(page, selectors, timeout=10000)
            if not found_sel:
                await browser.close()
                raise Exception("Tidak ada selector yang muncul dalam 10 detik; keluar sebagai gagal.")

            await self._send_telegram(f"🔎 Selector pertama ditemukan: `{found_sel}` — mulai klik dan capture")

            # attempts loop: click found selector and try to capture download; if no download, detect timer and repeat bruteforce
            attempts = 0
            while attempts < self.max_attempts:
                attempts += 1
                # re-evaluate locator each attempt
                try:
                    locator = page.locator(found_sel).first
                    if await locator.count() and await locator.is_visible():
                        # try default Playwright download capture
                        fname = await self._click_and_capture_download(page, locator, timeout=20000)
                        if fname:
                            await browser.close()
                            return fname
                        # if no download event, click (best-effort) and continue
                        try:
                            await locator.click()
                        except Exception:
                            pass
                    else:
                        # if the originally found selector disappeared, try to find any selector again quickly
                        new_sel = await self._wait_for_any_selector(page, selectors, timeout=3000)
                        if new_sel:
                            found_sel = new_sel
                            continue
                except Exception:
                    pass

                # after click attempt, check for countdown timer
                secs = await self._detect_countdown_seconds(page)
                if secs and secs > 0:
                    await self._send_telegram(f"⏳ Countdown detected: menunggu {secs}s sebelum bruteforce ulang")
                    await page.wait_for_timeout(min(secs * 1000 + 500, 60000))
                else:
                    # no timer detected: perform bruteforce scan (try all selectors in order)
                    await self._send_telegram("🔁 Tidak ada timer — melakukan bruteforce ulang dengan selector list")
                    for sel in selectors:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() and await loc.is_visible():
                                fname = await self._click_and_capture_download(page, loc, timeout=15000)
                                if fname:
                                    await browser.close()
                                    return fname
                                try:
                                    await loc.click()
                                except:
                                    pass
                                # short sniff window after each click
                                sniffed = await self._sniff_for_file(page, wait_ms=2000)
                                if sniffed:
                                    await browser.close()
                                    return sniffed
                        except Exception:
                            continue

                # if still no file, small wait then retry: reload to re-trigger scripts if needed
                await self._send_telegram(f"🔁 Percobaan {attempts} selesai tanpa file, reload dan coba lagi")
                try:
                    await page.reload(wait_until="domcontentloaded")
                except:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except:
                    pass
                # find a selector again before next attempt
                new_sel = await self._wait_for_any_selector(page, selectors, timeout=3000)
                if new_sel:
                    found_sel = new_sel
                else:
                    # if none found quickly, break early
                    await browser.close()
                    raise Exception("Selector hilang setelah reload; keluar sebagai gagal.")
            await browser.close()
            raise Exception("Gagal menemukan file setelah beberapa percobaan.")

    async def run(self):
        await self._send_telegram(f"🚀 Mulai: `{self.url}`")
        try:
            result = await self._run_browser()
            await self._send_telegram(f"✅ Selesai: `{result}`")
            return result
        except Exception as e:
            await self._send_telegram(f"💥 Error: `{e}`")
            print("Final error:", e)
            return None

# -------------------------
# CLI Runner
# -------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python downloader.py <url>")
        sys.exit(1)
    url = sys.argv[1]
    bot = DownloaderBotAsync(url)
    async def main():
        result = await bot.run()
        if result:
            print("Selesai:", result)
            try:
                with open("downloaded_filename.txt", "w") as f:
                    f.write(result)
            except Exception:
                pass
        else:
            print("Download gagal.")
    asyncio.run(main())
