import os
import subprocess
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
import time
import json
import re
import tempfile
import shutil
import math
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

class DownloaderBot:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        self.driver = None
        
    def close(self):
        """Cleanup eksplisit untuk menghindari zombie processes."""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        if os.path.exists(self.temp_download_dir):
            shutil.rmtree(self.temp_download_dir, ignore_errors=True)

    # =========================================================
    # --- 1. TELEGRAM & SIZE HELPERS ---
    # =========================================================

    def _human_readable_size(self, size_bytes):
        if not size_bytes: return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024))) if size_bytes > 0 else 0
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    def _send_telegram_message(self, message_text):
        if not self.bot_token or not self.owner_id: return None
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.owner_id, "text": message_text, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=10).json()
            self.initial_message_id = response.get('result', {}).get('message_id')
            return self.initial_message_id
        except: return None
            
    def _edit_telegram_message(self, message_text):
        if not self.initial_message_id: return
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, 
                   "text": message_text, "parse_mode": "Markdown"}
        try: requests.post(url, json=payload, timeout=10)
        except: pass 

    # =========================================================
    # --- 2. CORE DOWNLOADER (ARIA2C & MEGA) ---
    # =========================================================

    def _download_file_with_aria2c(self, urls, output_filename):
        self._edit_telegram_message(f"⬇️ **Aria2c:** Memulai download `{output_filename}`...")
        
        command = ['aria2c', '--allow-overwrite', '--file-allocation=none', '-x', '16', '-s', '16', '-c', 
                   '--async-dns=false', '--console-log-level=warn', '-o', output_filename]
        
        # Cari total size untuk progress bar
        total_size = None
        for u in urls:
            total_size = self._get_file_info(u)[1]
            if total_size: break

        process = subprocess.Popen(command + urls, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        last_size = 0
        last_update_time = time.time()
        start_time = time.time()

        try:
            while process.poll() is None:
                if os.path.exists(output_filename):
                    current_size = os.path.getsize(output_filename)
                    
                    # Stall Detection: Jika size tidak nambah dalam 60 detik, kill.
                    if current_size > last_size:
                        last_size = current_size
                        last_update_time = time.time()
                    elif time.time() - last_update_time > 60:
                        process.kill()
                        raise Exception("Download macet (Stalled) lebih dari 60 detik.")

                    # Progress update (per 10% atau tiap 5 detik)
                    if total_size:
                        percent = int(current_size * 100 // total_size)
                        if percent % 10 == 0:
                            self._edit_telegram_message(f"⬇️ **Downloading:** `{percent}%` ({self._human_readable_size(current_size)})")
                
                time.sleep(5)

            if process.returncode == 0:
                self._edit_telegram_message(f"✅ **Download Selesai:** `{output_filename}`")
                return output_filename
        except Exception as e:
            process.kill()
            raise e
        return None

    def _download_file_with_megatools(self, url):
        self._edit_telegram_message("⬇️ **Mega.nz:** Menggunakan `megatools`...")
        try:
            # Megatools langsung download ke working directory
            process = subprocess.run(['megatools', 'dl', url], capture_output=True, text=True)
            if process.returncode == 0:
                # Cari file terbaru yang baru didownload
                files = sorted([f for f in os.listdir('.') if os.path.isfile(f)], key=os.path.getmtime)
                return files[-1]
        except Exception as e:
            raise Exception(f"Megatools error: {str(e)}")

    # =========================================================
    # --- 3. SMART CLICKER & SCRAPING ENGINE ---
    # =========================================================

    def _get_file_info(self, url):
        """Revisi: Fallback GET jika HEAD gagal."""
        try:
            r = requests.head(url, allow_redirects=True, timeout=10)
            if r.status_code >= 400:
                r = requests.get(url, stream=True, timeout=10)
            
            ctype = r.headers.get('Content-Type', '').lower()
            is_file = 'text/html' not in ctype
            size = int(r.headers.get('Content-Length', 0))
            return is_file, size
        except: return False, 0

    def _handle_countdown(self, timeout=60):
        """Revisi: Loop murni sampai elemen timer hilang."""
        start = time.time()
        while time.time() - start < timeout:
            src = self.driver.page_source.lower()
            match = re.search(r'(\d+)\s*(seconds?|detik|sec|wait)', src)
            if match:
                self._edit_telegram_message(f"⏳ **Timer Detected:** Menunggu {match.group(1)}s...")
                time.sleep(3)
                continue
            break
        return True

    def smart_clicker(self, current_url, depth=0):
        if depth > 3: raise Exception("Deep Scraping Limit Reached.")
        
        self._edit_telegram_message(f"🔍 **Scanning Page** (Depth {depth})...")
        self.driver.get(current_url)
        self._handle_countdown()
        
        # Strategi 1: Cari link dengan ekstensi file langsung
        links = self.driver.find_elements(By.TAG_NAME, "a")
        for l in links:
            href = l.get_attribute("href")
            if href and any(ext in href.lower() for ext in ['.zip', '.rar', '.7z', '.apk', '.exe', '.mkv']):
                is_file, _ = self._get_file_info(href)
                if is_file: return self._download_file_with_aria2c([href], os.path.basename(urlparse(href).path))

        # Strategi 2: Bruteforce Click tombol "Download"
        selectors = [
            "//a[contains(translate(text(),'D','d'),'ownload')]",
            "//button[contains(translate(text(),'D','d'),'ownload')]",
            "//div[contains(@id,'download')]//a",
            "//a[contains(@class,'btn-download')]"
        ]
        
        for xpath in selectors:
            try:
                btn = self.driver.find_element(By.XPATH, xpath)
                if btn.is_displayed():
                    target_url = btn.get_attribute("href")
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(5)
                    
                    new_url = self.driver.current_url
                    if new_url != current_url:
                        is_file, _ = self._get_file_info(new_url if target_url is None else target_url)
                        if is_file:
                            return self._download_file_with_aria2c([new_url], "downloaded_file")
                        else:
                            return self.smart_clicker(new_url, depth + 1)
            except: continue
            
        raise Exception("Smart Clicker gagal menemukan endpoint.")

    # =========================================================
    # --- 4. SPECIAL HANDLERS ---
    # =========================================================

    def _process_sourceforge(self):
        self.driver.get(self.url)
        # Ambil mirror list via URL manipulasi
        p = urlparse(self.url)
        parts = p.path.split('/')
        proj, fpath = parts[2], '/'.join(parts[4:-1])
        mirror_url = f"https://sourceforge.net/settings/mirror_choices?projectname={proj}&filename={fpath}"
        
        self.driver.get(mirror_url)
        mirrors = WebDriverWait(self.driver, 10).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul#mirrorList li")))
        
        ids = [m.get_attribute("id") for m in mirrors if m.get_attribute("id")]
        # Generate direct links dari mirror IDs
        direct_links = [f"{self.url}?use_mirror={mid}" for mid in ids[:5]] # Ambil 5 mirror teratas
        return self._download_file_with_aria2c(direct_links, parts[-1])

    # =========================================================
    # --- 5. ORCHESTRATOR ---
    # =========================================================

    def run(self):
        self._send_telegram_message(f"🚀 **Job Started**\nURL: `{self.url}`")
        try:
            if "mega.nz" in self.url:
                return self._download_file_with_megatools(self.url)
            
            # Setup Selenium
            options = webdriver.ChromeOptions()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
            
            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            stealth(self.driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)

            if "sourceforge.net" in self.url:
                return self._process_sourceforge()
            else:
                return self.smart_clicker(self.url)

        except Exception as e:
            self._edit_telegram_message(f"❌ **Error:**\n`{str(e)[:200]}`")
            return None
        finally:
            self.close()

# Usage
# bot = DownloaderBot("https://example.com/file")
# bot.run()
