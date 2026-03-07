import os
import subprocess
import requests
import time
import re
import json
import math
import sys
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth

class SourceForgeDownloader:
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("OWNER_ID") # Sesuai dengan ENV di main.js
        self.initial_message_id = None
        self.driver = None

    def _send_telegram(self, text):
        if not self.bot_token or not self.owner_id:
            print(f"[Python]: {text}")
            return
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.owner_id, "text": text, "parse_mode": "Markdown"}
        try:
            res = requests.post(url, json=payload, timeout=10).json()
            self.initial_message_id = res.get('result', {}).get('message_id')
        except: pass

    def _edit_telegram(self, text):
        if not self.initial_message_id: return self._send_telegram(text)
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, "text": text, "parse_mode": "Markdown"}
        try: requests.post(url, json=payload, timeout=10)
        except: pass

    def _init_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        
        stealth(self.driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True)
        return True

    def _get_mirror_url(self, download_url):
        parsed = urlparse(download_url)
        path_parts = parsed.path.split('/')
        # Pola: /projects/NAME/files/PATH/TO/FILE/download
        project_name = path_parts[2]
        file_path = '/'.join(path_parts[4:-1])
        
        query = {'projectname': project_name, 'filename': file_path}
        return urlunparse((parsed.scheme, parsed.netloc, "/settings/mirror_choices", '', urlencode(query), ''))

    def _download_aria2(self, urls, filename):
        self._edit_telegram(f"🚀 **Aria2c:** Menarik file `{filename}` via multi-mirror...")
        cmd = ['aria2c', '-x16', '-s16', '-j16', '-k1M', '--file-allocation=none', '-o', filename] + urls
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            if "%" in line:
                # Kirim ke stdout agar ditangkap oleh main.js
                print(line.strip(), flush=True)
        
        process.wait()
        return filename if process.returncode == 0 else None

    def run(self):
        try:
            self._send_telegram("🐍 **Python Engine:** Memulai bypass SourceForge...")
            self._init_driver()
            
            # 1. Kunjungi halaman utama untuk trigger session
            self.driver.get(self.url)
            time.sleep(5)
            
            # 2. Ambil Nama File & Link Awal
            wait = WebDriverWait(self.driver, 20)
            btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.button.green")))
            ahref = btn.get_attribute('href')
            
            fname_el = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "div.file-info div.name, .file-info > div")))
            filename = fname_el.text.strip() or "downloaded_file"

            # 3. Ambil Mirror List
            mirror_page = self._get_mirror_url(self.url)
            self.driver.get(mirror_page)
            
            items = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul#mirrorList > li")))
            mirror_ids = [item.get_attribute("id") for item in items if item.get_attribute("id")]
            
            # Buat list URL mirror untuk Aria2c
            def build_url(base, m_id):
                p = urlparse(base)
                q = parse_qs(p.query)
                q['use_mirror'] = [m_id]
                return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))

            final_urls = [build_url(ahref, mid) for mid in mirror_ids[:5]] # Ambil 5 mirror terbaik

            # 4. Eksekusi Download
            self.driver.quit()
            result = self._download_aria2(final_urls, filename)
            
            if result:
                # Simpan nama file untuk dibaca GitHub Actions/Node.js
                with open("downloaded_filename.txt", "w") as f:
                    f.write(result)
                self._edit_telegram(f"✅ **SourceForge Selesai!**\nFile: `{result}`")
            
        except Exception as e:
            self._edit_telegram(f"❌ **Python Error:** {str(e)}")
            if self.driver: self.driver.quit()
            sys.exit(1)

if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else None
    if target_url:
        SourceForgeDownloader(target_url).run()
