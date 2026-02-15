import os
import asyncio
import re
import sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # List selector bruteforce (tambahin di sini kalau nemu pola baru)
        self.selectors = [
            "form[name='F1']",            # ApkAdmin Step 1
            "text='Download'",            # ApkAdmin Step 2
            "#downloadButton",            # Mediafire / Gofile
            ".downloadbtn",               # ApkAdmin / Generic
            "text='Download'",            # Generic
            "text='Download Now'",        # Generic
            "#download-btn",              # Generic
            "a.btn-primary",              # Generic link
            ".btn-success"                # Generic button
        ]

    async def _notify(self, text):
        print(f"[*] {text}")
        # Logika kirim pesan Telegram lu di sini...

    async def _handle_popups(self, context):
        """Menutup tab iklan otomatis agar tidak mengganggu."""
        pages = context.pages
        if len(pages) > 1:
            for extra_page in pages[1:]:
                await extra_page.close()

    async def _generic_handler(self, page, context):
        """Fungsi Bruteforce Selector untuk tembus berbagai situs."""
        for attempt in range(1, 4):  # Maksimal 3 kali 'pindah halaman'
            await self._notify(f"🔍 Mencoba memindai tombol (Percobaan ke-{attempt})...")
            await self._handle_popups(context)
            
            target_element = None
            found_selector = None

            # 1. Bruteforce scan selector
            for selector in self.selectors:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=5000):
                        target_element = el
                        found_selector = selector
                        break
                except: continue

            if not target_element:
                await self._notify("⚠️ Tidak ada tombol download terdeteksi di halaman ini.")
                break

            await self._notify(f"🎯 Menemukan selector: `{found_selector}`. Mencoba klik...")

            try:
                # 2. Siapkan listener download & navigasi secara simultan
                download_task = asyncio.create_task(page.wait_for_event("download", timeout=15000))
                
                # Aksi: Jika form F1, submit. Jika lainnya, klik.
                if found_selector == "form[name='F1']":
                    action_task = asyncio.create_task(page.evaluate("document.forms['F1'].submit()"))
                else:
                    action_task = asyncio.create_task(target_element.click())

                # 3. Cek apakah aksi menghasilkan download atau navigasi
                done, pending = await asyncio.wait(
                    [download_task, action_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Jika download langsung terdeteksi
                if download_task in done:
                    try:
                        download = await download_task
                        save_path = os.path.join(os.getcwd(), download.suggested_filename)
                        await download.save_as(save_path)
                        return save_path
                    except Exception as e:
                        await self._notify(f"❌ Download gagal: {e}")
                
                # Jika tidak download tapi navigasi terjadi (Halaman pindah)
                await self._notify("🔄 Tidak ada download langsung, menunggu navigasi halaman...")
                await page.wait_for_load_state("networkidle", timeout=15000)
                
                # Lanjut ke loop attempt berikutnya (Scan ulang di halaman baru)
                continue

            except PlaywrightTimeout:
                await self._notify("⏰ Timeout nunggu respon, mencoba scan ulang...")
                continue
            except Exception as e:
                await self._notify(f"❌ Error saat eksekusi: {str(e)[:50]}")
                break

        return None

    async def run(self):
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka URL: `{self.url}`")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                # Panggil sang pawang generic
                result_file = await self._generic_handler(page, context)
                
                if result_file:
                    await self._notify(f"✅ **Berhasil:** `{result_file}`")
                else:
                    await self._notify("❌ Gagal mendapatkan file setelah bruteforce.")
            
            finally:
                await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
