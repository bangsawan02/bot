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
    if stealth_async:
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
    m = re.search(r'filename="?([^\";]+)"?', cd)
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
    # APKADMIN 3-CLICK LOGIC
    # -------------------------
    async def _apkadmin_clicks(self, page):
        # 1) Free Download
        btn1 = page.locator("text=/.*Free Download.*/i").first
        if await btn1.is_visible():
            await self._send_telegram("🔘 Klik 1: Free Download")
            try:
                await btn1.click()
            except:
                pass

        await page.wait_for_timeout(5000)

        # 2) Generate Link
        btn2 = page.locator("text=/.*(Generate|Create|Get).*Link.*/i").first
        if await btn2.is_visible():
            await self._send_telegram("🔘 Klik 2: Generate Link")
            try:
                await btn2.click()
            except:
                pass

        await page.wait_for_timeout(5000)

        # 3) Click here to download
        btn3 = page.locator("text=/.*Click here to download.*/i").first
        if await btn3.is_visible():
            await self._send_telegram("🔘 Klik 3: Click here to download")
            try:
                async with page.expect_download(timeout=15000) as d:
                    await btn3.click()
                download = await d.value
                fname = download.suggested_filename or "downloaded_file"
                await download.save_as(fname)
                return fname
            except:
                pass

        return None

    # -------------------------
    # Sniffer fallback
    # -------------------------
    async def _sniffer_fallback(self, page):
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

        await page.wait_for_timeout(3000)

        if sniffed:
            target = sniffed[-1]
            headers = last_headers.get(target, {})
            fname = guess_name(headers, target)
            return await self._download_aria2(target, fname)

        return None

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

            # 3 tahap klik
            result = await self._apkadmin_clicks(page)
            if result:
                return result

            # fallback sniff
            result = await self._sniffer_fallback(page)
            if result:
                return result

            raise Exception("Tidak menemukan file setelah 3 klik + sniff.")

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
