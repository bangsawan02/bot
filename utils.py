import os
import asyncio
import re
import sys
import tempfile
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        # List selector bruteforce
        self.selectors = [
            "#filemanager_itemslist > div:nth-child(7) > div > div:nth-child(2) > div > button",
            "form[name='F1']",            # ApkAdmin Step 1
            "text='Download'",       # ApkAdmin Step 2 / Generic
            "#downloadButton",            # Mediafire / Gofile
            ".downloadbtn",               # Generic
            "text='Download'",            # Generic
            "text='Download Now'",        # Generic
            "#download-btn",              # Generic
            "a[href*='download']"         # Link yang mengandung kata download
        ]

    async def _notify(self, text):
        print(f"[*] {text}")

    async def _handle_popups(self, context):
        """Menutup tab iklan otomatis."""
        if len(context.pages) > 1:
            for extra_page in context.pages[1:]:
                await extra_page.close()

    async def _generic_handler(self, page, context):
        """Handler Bruteforce dengan tunggu selector maksimal 10 detik."""
        for attempt in range(1, 4):
            await self._notify(f"🔎 Memindai halaman (Percobaan ke-{attempt})...")
            await self._handle_popups(context)
            
            target_element = None
            found_selector = None

            # --- BRUTEFORCE SCAN DENGAN WAIT 10 DETIK ---
            # Kita gunakan loop untuk mengecek satu per satu dengan timeout singkat 
            # atau mencoba menunggu salah satu selector muncul.
            try:
                # Menunggu salah satu selector utama muncul (opsi tercepat)
                # Kita gabungkan semua selector jadi satu string untuk wait_for_selector
                combined_selectors = ", ".join(self.selectors[:5]) 
                await page.wait_for_selector(combined_selectors, state="visible", timeout=10000)
            except:
                await self._notify("⏳ Menunggu selector spesifik muncul...")

            # Scan ulang secara detail mana yang beneran visible
            for selector in self.selectors:
                try:
                    el = page.locator(selector).first
                    # Cek visible tanpa nunggu lagi karena sudah dipicu wait_for_selector di atas
                    if await el.is_visible():
                        target_element = el
                        found_selector = selector
                        break
                except: continue

            if not target_element:
                await self._notify("⚠️ Tidak ditemukan tombol download setelah menunggu 10 detik.")
                # Jika tidak ketemu, coba tunggu navigasi barangkali halaman auto-refresh
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    continue
                except: break

            await self._notify(f"🎯 Ketemu: `{found_selector}`. Mengeksekusi...")

            try:
                # Siapkan listener download
                download_task = asyncio.create_task(page.wait_for_event("download", timeout=20000))
                
                # Eksekusi Klik/Submit
                if "form" in found_selector:
                    action_task = asyncio.create_task(page.evaluate(f"document.querySelector(\"{found_selector}\").submit()"))
                else:
                    action_task = asyncio.create_task(target_element.click())

                # Tunggu mana yang duluan: Download atau Navigasi halaman baru
                done, pending = await asyncio.wait(
                    [download_task, action_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )

                if download_task in done:
                    download = await download_task
                    save_path = os.path.join(os.getcwd(), download.suggested_filename)
                    await download.save_as(save_path)
                    return save_path
                
                # Jika klik sukses tapi tidak ada download, nunggu halaman baru
                await self._notify("🔄 Halaman berubah, memindai ulang di halaman baru...")
                await page.wait_for_load_state("networkidle", timeout=10000)
                
            except Exception as e:
                await self._notify(f"❌ Gagal pada `{found_selector}`: {str(e)[:50]}")
                continue

        return None

    async def run(self):
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent, accept_downloads=True)
            page = await context.new_page()

            try:
                await self._notify(f"🌐 Membuka: {self.url}")
                await page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                
                file_path = await self._generic_handler(page, context)
                if file_path:
                    await self._notify(f"✅ SUKSES: File disimpan di `{file_path}`")
                else:
                    await self._notify("❌ GAGAL: Tidak bisa menemukan file.")
            finally:
                await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    asyncio.run(DownloaderBot(sys.argv[1]).run())
