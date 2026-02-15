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

# -------------------------
# Robust stealth import
# -------------------------
stealth_async = None
try:
    # PyPI layout: playwright_stealth/stealth.py
    from playwright_stealth.stealth import stealth_async
except Exception:
    try:
        # fallback: direct export (older variants)
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

def guess_name(headers: dict, url: str, suggested=None):
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

    # aria2c then requests fallback
    async def _download_aria2(self, url: str, name: str):
        if shutil.which("aria2c"):
            await self._send_telegram(f"⚡ Aria2c: `{name}`")
            cmd = [
                "aria2c", "-x", "16", "-s", "16", "-c",
                "--header=User-Agent: Mozilla/5.0",
                f"--header=Referer: {self.url}",
                "-o", name, url
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(name):
                return name
            print("aria2c failed:", stderr.decode(errors="ignore"))
        try:
            await self._send_telegram(f"⬇️ Requests fallback: `{name}`")
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                fname = guess_name(r.headers, r.url, suggested=name)
                with open(fname, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                return fname
        except Exception as e:
            print("requests fallback failed:", e)
        return None

    # detect and wait for JS-driven timers or readiness flags
    async def _detect_and_wait_timer(self, page, max_wait=30):
        """
        Wait for common JS-driven readiness signals:
        - networkidle
        - window.downloadReady === true
        - visible countdown element or numeric text
        - meta refresh
        Returns total waited seconds.
        """
        waited = 0
        # 1) wait for network idle briefly
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass

        # 2) check for JS readiness flag (downloadReady)
        try:
            ready = await page.evaluate("() => (typeof window.downloadReady !== 'undefined' && !!window.downloadReady)")
            if ready:
                return waited
        except:
            pass

        # 3) wait for visible countdown element or numeric text using wait_for_function
        try:
            # function returns remaining seconds if found, else 0
            js = """
            () => {
                // look for common countdown elements
                const sel = document.querySelector('#countdown, .countdown, .timer, [data-countdown], [data-seconds]');
                if (sel) {
                    const txt = (sel.innerText || sel.textContent || '').trim();
                    const m = txt.match(/(\\d{1,3})/);
                    if (m) return parseInt(m[1], 10);
                }
                // look for data attributes
                const el = document.querySelector('[data-countdown],[data-seconds],[data-timer]');
                if (el) {
                    const v = el.getAttribute('data-countdown') || el.getAttribute('data-seconds') || el.getAttribute('data-timer');
                    if (v) return parseInt(v, 10) || 0;
                }
                return 0;
            }
            """
            remaining = await page.wait_for_function(js, timeout=2000)
            rem_val = 0
            try:
                rem_val = int(await remaining.json_value())
            except:
                rem_val = 0
            if rem_val > 0:
                wait_ms = min(rem_val * 1000 + 500, max_wait * 1000)
                await self._send_telegram(f"⏳ Detected countdown: menunggu {rem_val}s")
                await page.wait_for_timeout(wait_ms)
                waited += rem_val
                return waited
        except Exception:
            pass

        # 4) meta refresh
        try:
            content = await page.content()
            m = re.search(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?(\d+)', content, flags=re.IGNORECASE)
            if m:
                secs = int(m.group(1))
                await self._send_telegram(f"⏳ Meta refresh detected: menunggu {secs}s")
                await page.wait_for_timeout(min(secs * 1000 + 500, max_wait * 1000))
                waited += secs
                return waited
        except:
            pass

        # 5) fallback small wait to allow JS to finish
        await page.wait_for_timeout(1000)
        return waited

    # APKAdmin 3-click flow with robust waits
    async def _apkadmin_clicks(self, page):
        # ensure JS rendering: wait for networkidle and for at least one of expected selectors
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass

        # wait for either Free Download or Generate Link to appear
        try:
            await page.wait_for_selector("text=/.*(Free Download|Generate|Create|Get).*Link.*/i", timeout=7000)
        except:
            # no obvious selector; continue anyway
            pass

        # 1) Free Download (if present)
        try:
            btn1 = page.locator("text=/.*Free Download.*/i").first
            if await btn1.count() and await btn1.is_visible():
                await self._send_telegram("🔘 Klik 1: Free Download")
                await btn1.click()
        except Exception:
            pass

        # detect and wait timers or readiness after first click
        await self._detect_and_wait_timer(page)

        # 2) Generate Link (if present)
        try:
            btn2 = page.locator("text=/.*(Generate|Create|Get).*Link.*/i").first
            if await btn2.count() and await btn2.is_visible():
                await self._send_telegram("🔘 Klik 2: Generate Link")
                # prefer wait_for_selector for any resulting download link element
                try:
                    await btn2.click()
                except:
                    pass
        except Exception:
            pass

        # detect and wait timers or readiness after second click
        await self._detect_and_wait_timer(page)

        # 3) Click here to download (explicit)
        try:
            # wait for the final download link/button to appear
            try:
                await page.wait_for_selector("text=/.*Click here to download.*/i", timeout=8000)
            except:
                pass
            btn3 = page.locator("text=/.*Click here to download.*/i").first
            if await btn3.count() and await btn3.is_visible():
                await self._send_telegram("🔘 Klik 3: Click here to download")
                try:
                    async with page.expect_download(timeout=20000) as d:
                        await btn3.click()
                    download = await d.value
                    fname = download.suggested_filename or "downloaded_file"
                    await download.save_as(fname)
                    return fname
                except PlaywrightTimeout:
                    pass
        except Exception:
            pass

        return None

    # sniffer fallback: capture file-like responses
    async def _sniffer_fallback(self, page, wait_ms=3000):
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
            except:
                pass

        page.on("response", on_response)
        await page.wait_for_timeout(wait_ms)
        if sniffed:
            target = sniffed[-1]
            headers = last_headers.get(target, {})
            fname = guess_name(headers, target)
            return await self._download_aria2(target, fname)
        return None

    # main browser orchestration with deterministic waits
    async def _run_browser(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await context.new_page()

            # apply stealth if available
            await apply_stealth(page)

            # navigate and wait for JS rendering (networkidle + selector/function)
            await page.goto(self.url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass

            # additional deterministic wait: wait for either known buttons or a readiness flag
            try:
                await page.wait_for_function(
                    """() => {
                        if (typeof window.downloadReady !== 'undefined' && window.downloadReady) return true;
                        if (document.querySelector('text, a, button')) return true;
                        return false;
                    }""",
                    timeout=8000
                )
            except:
                pass

            # Try APKAdmin 3-click flow (with timer detection between steps)
            result = await self._apkadmin_clicks(page)
            if result:
                await browser.close()
                return result

            # If not found, run sniffer fallback (after checking for timers)
            await self._detect_and_wait_timer(page)
            result = await self._sniffer_fallback(page)
            if result:
                await browser.close()
                return result

            # Retry: reload and attempt again with deterministic waits
            await self._send_telegram("🔁 Mencoba ulang: reload dan cek lagi...")
            try:
                await page.reload(wait_until="domcontentloaded")
            except:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except:
                pass
            await self._detect_and_wait_timer(page)
            result = await self._apkadmin_clicks(page)
            if result:
                await browser.close()
                return result

            # final sniff fallback
            result = await self._sniffer_fallback(page, wait_ms=4000)
            await browser.close()
            if result:
                return result

            raise Exception("Tidak menemukan file setelah semua langkah (render wait + 3 klik + sniff + retry).")

    async def run(self):
        await self._send_telegram(f"🚀 Mulai: `{self.url}`")
        try:
            result = await self._run_browser()
            await self._send_telegram(f"✅ Selesai: `{result}`")
            return result
        except Exception as e:
            await self._send_telegram(f"💥 Error: `{e}`")
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
