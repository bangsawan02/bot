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

# ============================================================
# STEALTH IMPORT — versi PyPI yang benar
# ============================================================
try:
    from playwright_stealth.stealth import stealth_async
except Exception:
    stealth_async = None


async def apply_stealth(page):
    if not stealth_async:
        print("Stealth not available, skipping.")
        return
    try:
        await stealth_async(page)
    except Exception as e:
        print("Stealth apply failed:", e)


# ============================================================
# Helper: filename
# ============================================================
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
    return name[:200] if name else "downloaded_file"


def extract_filename_from_cd(cd: str):
    if not cd:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, flags=re.IGNORECASE)
    return m.group(1) if m else None


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


# ============================================================
# DownloaderBotAsync
# ============================================================
class DownloaderBotAsync:
    def __init__(self, url: str):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.initial_message_id = None

    # -------------------------
    # Telegram
    # -------------------------
    async def _send_telegram(self, text: str):
        if not self.bot_token or not self.owner_id:
            print("[TELEGRAM NOT CONFIGURED]", text)
            return

        mode = "editMessageText" if self.initial_message_id else "sendMessage"
        api_url = f"https://api.telegram.org/bot{self.bot_token}/{mode}"

        payload = {
            "chat_id": self.owner_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        if self.initial_message_id:
            payload["message_id"] = self.initial_message_id

        try:
            res = await asyncio.to_thread(
                requests.post,
                api_url,
                json=payload,
                timeout=10
            )
            data = res.json()
            if not self.initial_message_id:
                self.initial_message_id = data.get('result', {}).get('message_id')
        except Exception as e:
            print("Telegram send failed:", e)

    # -------------------------
    # aria2 + requests fallback
    # -------------------------
    async def _download_aria2(self, url: str, name: str):
        if shutil.which("aria2c"):
            await self._send_telegram(f"⚡ Aria2c: `{name}`")
            cmd = [
                "aria2c",
                "-x", "16",
                "-s", "16",
                "-c",
                "--header=User-Agent: Mozilla/5.0",
                f"--header=Referer: {self.url}",
                "-o", name,
                url
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0 and os.path.exists(name):
                return name

            print("aria2c failed:", stderr.decode(errors="ignore"))

        # fallback requests
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

    # -------------------------
    # Bruteforce + Sniffer
    # -------------------------
    async def _bruteforce_once(self, page):
        sniffed = []
        last_headers = {}

        async def on_response(response):
            try:
                url = response.url
                headers = {k.lower(): v for k, v in response.headers.items()}
                last_headers[url] = headers

                if url.endswith(".js"):
                    return

                ctype = (headers.get("content-type") or "").lower()
                cd = (headers.get("content-disposition") or "").lower()

                if (
                    "application/" in ctype
                    or "octet-stream" in ctype
                    or "attachment" in cd
                    or url.lower().endswith((".apk", ".zip", ".exe", ".msi"))
                ):
                    sniffed.append(url)
            except:
                pass

        page.on("response", on_response)

        # Klik tombol download
        selectors = [
            "text=/.*(Free Download|Download).*/i",
            "a[href*='download']",
            "button:has-text('Download')",
            "a[href$='.apk']"
        ]

        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.is_visible():
                try:
                    async with page.expect_download(timeout=8000) as d:
                        await loc.click()
                    download = await d.value
                    fname = download.suggested_filename or "downloaded_file"
                    await download.save_as(fname)
                    return fname
                except:
                    pass

        # Klik kedua (Generate Link)
        second = page.locator("text=/.*(Generate|Create|Get).*Link.*/i").first
        if await second.is_visible():
            try:
                async with page.expect_download(timeout=8000) as d:
                    await second.click()
                download = await d.value
                fname = download.suggested_filename or "downloaded_file"
                await download.save_as(fname)
                return fname
            except:
                pass

        # fallback sniffed
        if sniffed:
            target = sniffed[-1]
            headers = last_headers.get(target, {})
            fname = guess_name(headers, target)
            return await self._download_aria2(target, fname)

        return None

    # -------------------------
    # Bruteforce multi-attempt
    # -------------------------
    async def _bruteforce(self, page):
        for attempt in range(1, 4):
            await self._send_telegram(f"🔎 Attempt {attempt}/3...")
            result = await self._bruteforce_once(page)
            if result:
                return result
            await page.wait_for_timeout(2000)
        raise Exception("Bruteforce gagal total setelah 3 percobaan.")

    # -------------------------
    # Browser runner
    # -------------------------
    async def _run_browser(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            )
            page = await context.new_page()

            await apply_stealth(page)

            await page.goto(self.url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            try:
                return await self._bruteforce(page)
            finally:
                await browser.close()

    async def run(self):
        await self._send_telegram(f"🚀 Mulai: `{self.url}`")
        try:
            result = await self._run_browser()
            await self._send_telegram(f"✅ Selesai: `{result}`")
            return result
        except Exception as e:
            await self._send_telegram(f"💥 Error: `{e}`")
            return None


# ============================================================
# CLI Runner
# ============================================================
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
            with open("downloaded_filename.txt", "w") as f:
                f.write(result)
        else:
            print("Download gagal.")

    asyncio.run(main())
