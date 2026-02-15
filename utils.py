import os
import asyncio
import subprocess
import requests
import re
import tempfile
import shutil
import sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        # Ambil dari environment variable atau isi manual di sini
        self.bot_token = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER", "YOUR_CHAT_ID")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

    # =========================================================
    # --- UTILS & TELEGRAM ---
    # =========================================================

    def _human_size(self, b):
        if not b: return "0 B"
        for unit in ['B','KB','MB','GB']:
            if b < 1024: return f"{b:.2f} {unit}"
            b /= 1024

    async def _notify(self, text):
        """Update status ke Telegram secara Async."""
        print(f"[*] {text}")
        if not self.bot_token or not self.owner_id: return

        loop = asyncio.get_event_loop()
        if self.initial_message_id is None:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
            try:
                res = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10).json())
                self.initial_message_id = res.get('result', {}).get('message_id')
            except Exception as e: print(f"TG Error: {e}")
        else:
            url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
            payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, "text": text, "parse_mode": "Markdown"}
            try:
                await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10))
            except Exception as e: print(f"TG Error: {e}")

    # =========================================================
    # --- EXTERNAL TOOLS HANDLER ---
    # =========================================================

    async def _run_aria2c(self, download_url, output_filename, referer=None):
        """Eksekusi Aria2c dengan monitoring progress."""
        await self._notify(f"⬇️ **Aria2c:** Memulai download `{output_filename}`")
        
        cmd = [
            'aria2c', 
            '--allow-overwrite=true',
            '--file-allocation=none',
            '--user-agent', self.user_agent,
            '-x', '16', '-s', '16', '-j', '16',
            '-o', output_filename
        ]
        if referer:
            cmd.extend(['--header', f'Referer: {referer}'])
        
        cmd.append(download_url)

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )

        # Monitor output aria2c untuk mencari progress
        while True:
            line = await process.stdout.readline()
            if not line: break
            decoded_line = line.decode().strip()
            
            # Cari pola progress seperti (10%)
            if "[" in decoded_line and "%]" in decoded_line:
                match = re.search(r'\((\d+)%\)', decoded_line)
                if match:
                    percent = match.group(1)
                    if int(percent) % 20 == 0: # Update tiap kelipatan 20%
                        await self._notify(f"⬇️ **Aria2c Progress:** `{percent}%` untuk `{output_filename}`")

        await process.wait()
        if process.returncode == 0:
            await self._notify(f"✅ **Selesai:** `{output_filename}` berhasil diunduh.")
            return output_filename
        else:
            await self._notify(f"❌ **Aria2c Error:** Exit code {process.returncode}")
            return None

    # =========================================================
    # --- BROWSER SCRAPING (STEALTH) ---
    # =========================================================

    async def _handle_pixeldrain(self):
        """Logika khusus Pixeldrain menggunakan API."""
        match = re.search(r"/(?:u|file)/([a-zA-Z0-9]+)", self.url)
        if not match: return None
        
        file_id = match.group(1)
        api_info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
        
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: requests.get(api_info_url).json())
            if info.get("success"):
                filename = info.get("name")
                size = info.get("size")
                dl_url = f"https://pixeldrain.com/api/file/{file_id}?download"
                await self._notify(f"📦 **Pixeldrain:** `{filename}` ({self._human_size(size)})")
                return await self._run_aria2c(dl_url, filename, referer="https://pixeldrain.com/")
        except: pass
        return None

    async def _handle_playwright_sites(self):
        """Logika umum menggunakan Playwright Stealth (Gofile, ApkAdmin, dll)."""
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True)
            # Buat context dengan Stealth
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            # Testing Stealth Status
            is_stealth = await page.evaluate("navigator.webdriver")
            print(f"[*] Navigator.webdriver: {is_stealth}")

            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                if "apkadmin.com" in self.url:
                    await self._notify("🔍 **ApkAdmin:** Menekan tombol generate...")
                    # Submit form F1 (pemicu download di ApkAdmin)
                    await page.evaluate("document.forms['F1'].submit()")
                    
                # Menangkap event download yang terpicu otomatis/klik
                async with page.expect_download(timeout=60000) as download_info:
                    # Klik tombol download yang umum jika tidak otomatis terpicu
                    for selector in ["#downloadButton", "text='Download'", ".btn-download"]:
                        try:
                            btn = page.locator(selector).first
                            if await btn.is_visible():
                                await btn.click()
                        except: pass
                
                download = await download_info.value
                save_path = os.path.join(os.getcwd(), download.suggested_filename)
                await download.save_as(save_path)
                return save_path

            except Exception as e:
                await self._notify(f"❌ **Browser Error:** {str(e)[:100]}")
            finally:
                await browser.close()

    # =========================================================
    # --- MAIN RUNNER ---
    # =========================================================

    async def run(self):
        await self._notify(f"⏳ **Memproses URL:** `{self.url}`")

        # 1. Cek Pixeldrain (API lebih cepat daripada Browser)
        if "pixeldrain.com" in self.url:
            result = await self._handle_pixeldrain()
            if result: return result

        # 2. Cek Mega.nz
        if "mega.nz" in self.url:
            # Megatools biasanya sinkron, jalankan di executor
            await self._notify("⬇️ **Megatools:** Memulai download MEGA...")
            # (Implementasi megatools dl ...)
            return "mega_downloaded"

        # 3. Gunakan Playwright Stealth untuk sisa situs lainnya
        return await self._handle_playwright_sites()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python downloader_async.py <URL>")
        sys.exit(1)
        
    input_url = sys.argv[1]
    bot = DownloaderBot(input_url)
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
