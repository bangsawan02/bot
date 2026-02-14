import os
import subprocess
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
import time
import json
import re
import tempfile
import shutil
import math
from urllib.parse import urlparse

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        self.driver = None

    def close(self):
        """Cleanup total: Driver mati, folder temp hilang."""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        if os.path.exists(self.temp_download_dir):
            shutil.rmtree(self.temp_download_dir, ignore_errors=True)

    # =========================================================
    # --- HELPERS (Telegram, Size, Info) ---
    # =========================================================

    def _send_telegram(self, text):
        if not self.bot_token or not self.owner_id: return
        url = f"https://api.telegram.org/bot{self.bot_token}/{'editMessageText' if self.initial_message_id else 'sendMessage'}"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        if self.initial_message_id: payload["message_id"] = self.initial_message_id
        try:
            res = requests.post(url, json=payload, timeout=10).json()
            if not self.initial_message_id: self.initial_message_id = res.get('result', {}).get('message_id')
        except: pass

    def _get_file_info(self, url):
        """Pengecekan link sakti: HEAD dengan fallback GET Stream."""
        try:
            # Spoof User-Agent biar gak kena 403
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
            r = requests.head(url, allow_redirects=True, timeout=10, headers=headers)
            if r.status_code >= 400:
                r = requests.get(url, stream=True, timeout=10, headers=headers)
            
            ctype = r.headers.get('Content-Type', '').lower()
            # Jika bukan HTML, berarti kemungkinan besar ini file
            is_file = 'text/html' not in ctype or any(ext in url.lower() for ext in ['.zip', '.rar', '.7z', '.apk', '.bin'])
            size = int(r.headers.get('Content-Length', 0))
            return is_file, size
        except: return False, 0

    # =========================================================
    # --- CORE DOWNLOADERS (Aria2c & Mega) ---
    # =========================================================

    def _download_aria2c(self, urls, filename):
        self._send_telegram(f"⚡ **Aria2c:** Memulai unduhan `{filename}`...")
        
        # Output path di folder saat ini agar mudah diakses caller script
        cmd = ['aria2c', '--allow-overwrite', '--file-allocation=none', '-x', '16', '-s', '16', '-c', '-o', filename]
        
        # Ambil total size buat progress
        _, total_size = self._get_file_info(urls[0])
        
        process = subprocess.Popen(cmd + urls, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        last_size = 0
        last_time = time.time()
        
        try:
            while process.poll() is None:
                if os.path.exists(filename):
                    curr_size = os.path.getsize(filename)
                    # Stall Detection: Jika macet > 45 detik, bunuh.
                    if curr_size > last_size:
                        last_size = curr_size
                        last_time = time.time()
                    elif time.time() - last_time > 45:
                        process.kill()
                        raise Exception("Download Stall: Kecepatan 0 selama 45 detik.")
                    
                    if total_size > 0:
                        p = int(curr_size * 100 // total_size)
                        if p % 20 == 0: # Update tiap kelipatan 20% biar gak spam
                            self._send_telegram(f"📥 **Progress:** `{p}%` | `{filename}`")
                time.sleep(5)
            
            if process.returncode == 0:
                return filename
        except Exception as e:
            process.kill()
            raise e
        return None

    def _download_mega(self, url):
        self._send_telegram("☁️ **Mega.nz:** Menggunakan `megatools`...")
        try:
            # Download langsung ke current dir
            subprocess.run(['megatools', 'dl', url], check=True)
            # Megatools gak kasih output nama file yang mudah, kita cari file terbaru
            files = sorted([f for f in os.listdir('.') if os.path.isfile(f)], key=os.path.getmtime)
            return files[-1]
        except: raise Exception("Gagal mengunduh dari Mega.nz")

    # =========================================================
    # --- THE BRUTEFORCE ENGINE (CDP + Smart Clicker) ---
    # =========================================================

    def _handle_countdown(self):
        """Menunggu timer hilang secara dinamis."""
        start = time.time()
        while time.time() - start < 60:
            src = self.driver.page_source.lower()
            if any(x in src for x in ['wait', 'seconds', 'detik', 'readying']):
                time.sleep(2)
                continue
            break

    def smart_bruteforce(self, url):
        self._send_telegram("🕵️ **Bruteforce Mode:** Mengendus Network Log...")
        self.driver.get(url)
        self._handle_countdown()

        # List selector tombol download paling umum
        selectors = [
            "//a[contains(translate(text(),'D','d'),'ownload')]",
            "//button[contains(translate(text(),'D','d'),'ownload')]",
            "//a[contains(@href,'download')]",
            "//*[contains(@class,'btn') and contains(@class,'download')]"
        ]

        # Ambil semua element yang cocok
        elements = self.driver.find_elements(By.XPATH, " | ".join(selectors))
        
        for el in elements:
            try:
                if not el.is_displayed(): continue
                
                # Klik via JS (Bypass overlay iklan)
                self.driver.execute_script("arguments[0].click();", el)
                time.sleep(4) # Tunggu trigger network

                # Bongkar Performance Log buat cari link direct
                logs = self.driver.get_log('performance')
                for entry in logs:
                    msg = json.loads(entry['message'])['message']
                    if msg['method'] == 'Network.requestWillBeSent':
                        req_url = msg['params']['request']['url']
                        
                        # Filter link yang mencurigakan sebagai file
                        if any(ext in req_url.lower() for ext in ['.zip', '.rar', '.7z', '.apk', '.bin', '.mkv', '.mp4']):
                            is_file, _ = self._get_file_info(req_url)
                            if is_file:
                                fname = os.path.basename(urlparse(req_url).path) or "downloaded_file"
                                return self._download_aria2c([req_url], fname)
            except: continue
        
        raise Exception("Bruteforce gagal: Tidak ditemukan link file di network layer.")

    # =========================================================
    # --- HANDLER: SOURCEFORGE ---
    # =========================================================

    def _process_sourceforge(self):
        self._send_telegram("🌀 **SourceForge:** Mencari Mirror tercepat...")
        p = urlparse(self.url)
        path_parts = p.path.split('/')
        # Contoh: /projects/rextls/files/v1.zip/download -> proj: rextls, file: v1.zip
        proj = path_parts[2]
        f_name = path_parts[-2] if 'download' in path_parts[-1] else path_parts[-1]
        
        # Build URL mirror choices
        mirror_url = f"https://sourceforge.net/settings/mirror_choices?projectname={proj}&filename={f_name}"
        self.driver.get(mirror_url)
        
        mirrors = WebDriverWait(self.driver, 10).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul#mirrorList li")))
        # Ambil 3 mirror pertama untuk download paralel di aria2c
        m_ids = [m.get_attribute("id") for m in mirrors if m.get_attribute("id")][:3]
        direct_urls = [f"{self.url}?use_mirror={mid}" for mid in m_ids]
        
        return self._download_aria2c(direct_urls, f_name)

    # =========================================================
    # --- MAIN RUNNER ---
    # =========================================================

    def run(self):
        try:
            if "mega.nz" in self.url:
                return self._download_mega(self.url)

            # Setup Chrome dengan Performance Logging diaktifkan
            opts = webdriver.ChromeOptions()
            opts.add_argument('--headless=new')
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
            # Paksa download tidak memunculkan popup
            opts.add_experimental_option("prefs", {"download.prompt_for_download": False})

            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            stealth(self.driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)

            if "sourceforge.net" in self.url:
                return self._process_sourceforge()
            else:
                return self.smart_bruteforce(self.url)

        except Exception as e:
            self._send_telegram(f"❌ **Error:** `{str(e)}`")
            return None
        finally:
            self.close()
