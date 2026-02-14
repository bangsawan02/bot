import os
import subprocess
import requests
import time
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.initial_message_id = None

    # =========================================================
    # --- HELPERS ---
    # =========================================================

    def _send_telegram(self, text):
        if not self.bot_token or not self.owner_id: return
        mode = "editMessageText" if self.initial_message_id else "sendMessage"
        api_url = f"https://api.telegram.org/bot{self.bot_token}/{mode}"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        if self.initial_message_id: payload["message_id"] = self.initial_message_id
        
        try:
            res = requests.post(api_url, json=payload, timeout=10).json()
            if not self.initial_message_id:
                self.initial_message_id = res.get('result', {}).get('message_id')
        except: pass

    # =========================================================
    # --- CORE ENGINES ---
    # =========================================================

    def _download_aria2c(self, urls, filename):
        """Dipakai khusus untuk SourceForge & Direct Links karena lebih kencang."""
        self._send_telegram(f"⚡ **Aria2c:** Mengunduh `{filename}` via multi-connection...")
        cmd = ['aria2c', '--allow-overwrite', '-x', '16', '-s', '16', '-c', '-o', filename]
        process = subprocess.run(cmd + urls, capture_output=True, text=True)
        return filename if process.returncode == 0 else None

    def _download_mega(self, url):
        """Wrapper Megatools tetap yang terbaik buat Mega."""
        self._send_telegram("☁️ **Mega.nz:** Menggunakan `megatools`...")
        try:
            subprocess.run(['megatools', 'dl', url], check=True)
            files = sorted([f for f in os.listdir('.') if os.path.isfile(f)], key=os.path.getmtime)
            return files[-1]
        except: return None

    # =========================================================
    # --- PLAYWRIGHT HANDLERS ---
    # =========================================================

    def _process_sourceforge(self, page):
        """Ambil mirror SourceForge dan lempar ke Aria2c."""
        self._send_telegram("🌀 **SourceForge:** Mencari mirror...")
        page.goto(self.url)
        
        # Manipulasi URL untuk dapet list mirror
        proj_match = re.search(r'/projects/([^/]+)/files/(.*?)(/download|$)', self.url)
        if proj_match:
            proj, fpath = proj_match.group(1), proj_match.group(2)
            mirror_url = f"https://sourceforge.net/settings/mirror_choices?projectname={proj}&filename={fpath}"
            page.goto(mirror_url)
            
            # Ambil 3 ID mirror pertama
            mirrors = page.locator("ul#mirrorList li").all()
            ids = [m.get_attribute("id") for m in mirrors if m.get_attribute("id")][:3]
            direct_urls = [f"{self.url}?use_mirror={mid}" for mid in ids]
            return self._download_aria2c(direct_urls, fpath.split('/')[-1])
        return None

    def _process_generic(self, page):
        """Bruteforce Klik & Tangkap Download Event (Solusi Apkadmin, dkk)."""
        self._send_telegram("🕵️ **Bruteforce:** Menunggu event download...")
        
        try:
            page.goto(self.url, wait_until="domcontentloaded")
            
            # 1. Tunggu timer/countdown (jika ada)
            page.wait_for_timeout(5000) 

            # 2. Setup Download Listener
            # Kita 'mendengarkan' browser. Begitu ada stream file masuk, kita tangkap.
            with page.expect_download(timeout=120000) as download_info:
                
                # List selector tombol yang mungkin memicu download
                selectors = [
                    "text=/.*[Dd]ownload.*/", 
                    "#downloadbtn", 
                    ".downloadbtn",
                    "button:has-text('Start')",
                    "a[href*='download']"
                ]
                
                # Coba klik satu per satu sampai trigger download muncul
                for selector in selectors:
                    try:
                        btn = page.locator(selector).first
                        if btn.is_visible():
                            btn.click(no_wait_after=True) # Jangan tunggu navigasi, tunggu download
                    except: continue

                download = download_info.value
                filename = download.suggested_filename
                
                self._send_telegram(f"📥 **Streaming:** `{filename}` sedang ditarik...")
                
                final_path = os.path.join(os.getcwd(), filename)
                download.save_as(final_path)
                return filename

        except PlaywrightTimeout:
            raise Exception("Timeout: Browser tidak mendeteksi adanya file yang dikirim server.")
        except Exception as e:
            raise e

    # =========================================================
    # --- ORCHESTRATOR ---
    # =========================================================

    def run(self):
        # Jalur cepat tanpa browser
        if "mega.nz" in self.url:
            return self._download_mega(self.url)

        with sync_playwright() as p:
            # Launch dengan user-agent asli agar tidak terdeteksi bot
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            try:
                if "sourceforge.net" in self.url:
                    result = self._process_sourceforge(page)
                else:
                    result = self._process_generic(page)
                
                if result:
                    self._send_telegram(f"✅ **Selesai:** `{result}`")
                return result

            except Exception as e:
                self._send_telegram(f"❌ **Error:** `{str(e)}`")
                return None
            finally:
                browser.close()
