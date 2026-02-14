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
import glob
import math
import sys
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

# =========================================================
# CLASS UTAMA: DownloaderBot
# =========================================================

class DownloaderBot:
    """
    Mengelola seluruh proses download menggunakan logika "Smart Clicker"
    untuk semua web umum, dengan handler khusus untuk Mega dan SourceForge.
    """
    
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        self.driver = None
        
    def __del__(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        shutil.rmtree(self.temp_download_dir, ignore_errors=True)
        
    # =========================================================
    # --- 1. METODE BANTUAN TELEGRAM & UMUM ---
    # =========================================================

    def _human_readable_size(self, size_bytes):
        if size_bytes is None or size_bytes == 0: return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = int(math.floor(math.log(size_bytes, 1024))) if size_bytes > 0 else 0
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2) if p > 0 else 0
        return f"{s} {size_name[i]}"

    def _send_telegram_message(self, message_text):
        if not self.bot_token or not self.owner_id:
            print("Peringatan: Notifikasi Telegram dinonaktifkan.")
            return None
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.owner_id, "text": message_text, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            response_json = response.json()
            self.initial_message_id = response_json.get('result', {}).get('message_id')
            return self.initial_message_id
        except Exception as e:
            print(f"Gagal mengirim pesan Telegram: {e}")
            return None
            
    def _edit_telegram_message(self, message_text):
        if not self.bot_token or not self.owner_id or not self.initial_message_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, 
                   "text": message_text, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass 

    def _get_total_file_size_safe(self, url):
        try:
            response = requests.head(url, allow_redirects=True, timeout=10)
            response.raise_for_status()
            content_length = response.headers.get('Content-Length')
            if content_length: return int(content_length)
        except requests.exceptions.RequestException:
            pass 
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                if 'Content-Length' in r.headers:
                    return int(r.headers['Content-Length'])
        except requests.exceptions.RequestException:
            pass
        return None

    def _extract_filename_from_url_or_header(self, download_url):
        file_name = None
        try:
            head_response = requests.head(download_url, allow_redirects=True, timeout=10)
            head_response.raise_for_status()
            cd_header = head_response.headers.get('Content-Disposition')
            if cd_header:
                fname_match = re.search(r'filename\*?=["\']?(?:utf-8\'\')?([^"\';]+)["\']?', cd_header, re.I)
                if fname_match:
                    file_name = fname_match.group(1).strip()
                    file_name = re.sub(r'[^\x00-\x7F]+', '', file_name)
            
            if not file_name:
                url_path = urlparse(download_url).path
                file_name = url_path.split('/')[-1]
                
        except requests.exceptions.RequestException:
            url_path = urlparse(download_url).path
            file_name = url_path.split('/')[-1]
            
        return file_name if file_name else "unknown_file"

    # =========================================================
    # --- 2. METODE DOWNLOAD INTI (ARIA2C & MEGATOOLS) ---
    # =========================================================

    def _download_file_with_aria2c(self, urls, output_filename):
        print(f"Memulai unduhan {output_filename} dengan aria2c.")
        total_size = None
        command = ['aria2c', '--allow-overwrite', '--file-allocation=none', '--console-log-level=warn', 
                   '--summary-interval=0', '-x', '16', '-s', '16', '-c', '--async-dns=false', 
                   '--log-level=warn', '--continue', '--input-file', '-', '-o', output_filename]
        
        process = None
        try:
            self._send_telegram_message(f"⬇️ Download dimulai: `{output_filename}`")
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            for url in urls:
                total_size = self._get_total_file_size_safe(url)
                if total_size is not None:
                    process.stdin.write(url + '\n')
                    break
            process.stdin.close()
            
            start_time = time.time()
            timeout = 300
            last_notified_percent = 0
            
            while time.time() - start_time < timeout:
                if os.path.exists(output_filename):
                    current_size = os.path.getsize(output_filename)
                    if total_size is not None and total_size > 0:
                        percent_now = int(current_size * 100 // total_size)
                        should_update_50 = (percent_now >= 50 and last_notified_percent < 50)
                        should_update_100 = (percent_now >= 100)

                        if should_update_50 or should_update_100:
                            self._edit_telegram_message(f"⬇️ Download `{output_filename}` — {percent_now}% ({self._human_readable_size(current_size)}/{self._human_readable_size(total_size)})")
                            last_notified_percent = percent_now
                            
                    if (total_size is not None and current_size >= total_size):
                        if process.poll() is None:
                            process.terminate()
                            time.sleep(2)
                            if process.poll() is None: process.kill()
                        return output_filename
                        
                if process.poll() is not None:
                    if os.path.exists(output_filename) and os.path.getsize(output_filename) > 0:
                        if total_size is None or os.path.getsize(output_filename) > total_size:
                            total_size = os.path.getsize(output_filename)
                        self._edit_telegram_message(f"✅ Download Selesai. `{output_filename}` ({self._human_readable_size(total_size)})")
                        return output_filename
                    return None
                    
                time.sleep(3)
            
            if process and process.poll() is None:
                process.terminate()
                time.sleep(1)
                process.kill()
                
        except Exception as e:
            if process and process.poll() is None:
                process.terminate()
                time.sleep(1)
                process.kill()
                
        return None

    def _download_file_with_megatools(self, url):
        print(f"Mengunduh file dari MEGA dengan megatools: {url}")
        original_cwd = os.getcwd()
        temp_dir = tempfile.mkdtemp()
        filename = None
        self._send_telegram_message("⬇️ **Mulai mengunduh...**\n`megatools` sedang mengunduh file.")
        
        try:
            os.chdir(temp_dir)
            process = subprocess.Popen(['megatools', 'dl', url], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            last_notified_percent = 0
            progress_regex = re.compile(r'(\d+\.\d+)%\s+of\s+.*\((\d+\.\d+)\s*(\wB)\)')
            
            while True:
                line = process.stdout.readline()
                if not line: break
                
                match = progress_regex.search(line)
                if match:
                    percent_now = math.floor(float(match.group(1)))
                    current_size_str = match.group(2)
                    current_unit = match.group(3)
                    
                    if percent_now >= 50 and last_notified_percent < 50 or percent_now == 100:
                        last_notified_percent = percent_now
                        progress_message = f"⬇️ **Mulai mengunduh...**\nUkuran file: `{current_size_str} {current_unit}`\n\nProgres: `{percent_now}%`"
                        self._edit_telegram_message(progress_message)
                        
            process.wait()
            if process.returncode != 0:
                error_output = process.stderr.read()
                raise subprocess.CalledProcessError(process.returncode, process.args, stderr=error_output)
                
            downloaded_files = os.listdir('.')
            downloaded_files = [f for f in downloaded_files if not f.endswith('.megatools')]
            
            if len(downloaded_files) == 1:
                filename = downloaded_files[0]
                self._edit_telegram_message(f"✅ **MEGA: Unduhan selesai!**\nFile: `{filename}`\n\n**➡️ Mulai UPLOADING...**")
                return filename
            else:
                return None
        except Exception as e:
            self._edit_telegram_message(f"❌ **`megatools` gagal mengunduh file.**\n\nDetail: {str(e)[:200]}...")
            return None
        finally:
            os.chdir(original_cwd)
            if filename and os.path.exists(os.path.join(temp_dir, filename)):
                shutil.move(os.path.join(temp_dir, filename), os.path.join(original_cwd, filename))
            shutil.rmtree(temp_dir, ignore_errors=True)


    # =========================================================
    # --- 3. METODE SELENIUM & SMART CLICKER (DEEP SCRAPING) ---
    # =========================================================

    def _initialize_selenium_driver(self):
        chrome_prefs = {
            "download.default_directory": self.temp_download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        
        options = webdriver.ChromeOptions()
        options.add_experimental_option("prefs", chrome_prefs)
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled') 
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'}) 
        
        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            
            stealth(self.driver,
                    languages=["en-US", "en"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True)
            
            self.driver.set_page_load_timeout(60) 
            return True
        except Exception as e:
            print(f"❌ Gagal inisialisasi Selenium Driver: {e}")
            return False

    def _wait_for_network_idle(self):
        self._edit_telegram_message("⏳ Menunggu halaman stabil (Network Idle)...")
        time.sleep(3) 

    def _handle_countdown(self, timeout=40):
        start_wait = time.time()
        self._edit_telegram_message("⏳ Mencari Countdown Timer/Proteksi halaman...")
        while time.time() - start_wait < timeout:
            page_text = self.driver.page_source.lower()
            match = re.search(r'(\d+)\s*(seconds?|detik|sec)', page_text)
            if match:
                seconds = int(match.group(1))
                self._edit_telegram_message(f"⏳ Countdown terdeteksi! Menunggu {seconds} detik...")
                time.sleep(seconds + 2)
                return True
            time.sleep(2)
            break
        return False

    def _is_direct_link(self, url):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=5)
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                return False, url
            return True, url
        except:
            return False, url

    def smart_clicker(self, current_url, depth=0):
        """
        Logika Deep Scraping pengganti logic hardcoded.
        Berjalan secara rekursif mencari tombol/form sampai menemukan file.
        """
        if depth > 3:
            raise Exception("Gagal: Terlalu banyak kedalaman halaman (Looping HTML).")

        self._edit_telegram_message(f"🔍 **[Smart Clicker]** Menganalisis Halaman (Level {depth})...")
        self.driver.get(current_url)
        
        self._wait_for_network_idle()
        self._handle_countdown()

        # Daftar Bruteforce Omni-Selector
        selectors = [
            "//a[contains(translate(text(), 'DOWNLOAD', 'download'), 'download')]",
            "//button[contains(translate(text(), 'DOWNLOAD', 'download'), 'download')]",
            "//input[@type='submit' and contains(translate(@value, 'DOWNLOAD', 'download'), 'download')]",
            "//a[contains(@class, 'download')]",
            "//div[contains(@class, 'download')]//a",
            "//div[contains(@id, 'download')]//a",
            "//a[contains(@id, 'download')]",
            "//button[contains(@id, 'download')]"
        ]

        found_link = None
        
        for xpath in selectors:
            try:
                elements = self.driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if el.is_displayed():
                        # Coba tangkap Href-nya dulu
                        href = el.get_attribute("href")
                        if href and "javascript" not in href:
                            found_link = href
                            break
                        
                        # Jika tidak ada href (biasanya Button/Form), Paksa Klik dengan JS
                        self._edit_telegram_message("🎯 Menemukan tombol, mencoba mengklik (Bypass JS)...")
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
                        self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(3) # Tunggu aksi klik bereaksi
                        
                        # Cek apakah URL Browser berubah
                        new_url = self.driver.current_url
                        if new_url != current_url:
                            found_link = new_url
                            break
                if found_link: break
            except: 
                continue

        # Keamanan Tambahan: Periksa CDP Network Logs
        # Jika JS Click tadi mentrigger direct download di background (seperti Gofile/ApkAdmin)
        if not found_link:
            try:
                logs = self.driver.get_log('performance')
                for entry in logs:
                    msg = json.loads(entry['message'])['message']
                    if msg.get('method') == 'Network.responseReceived':
                        resp = msg['params']['response']
                        content_type = resp.get('mimeType', '').lower()
                        # Jika menemukan file biner di log jaringan
                        if 'application/' in content_type or 'octet-stream' in content_type or 'zip' in content_type:
                            if 'html' not in content_type:
                                found_link = resp.get('url')
                                break
            except: pass

        if found_link:
            self._edit_telegram_message(f"🔗 Menemukan link potensial!\nMelakukan verifikasi...")
            is_file, final_url = self._is_direct_link(found_link)
            
            if is_file:
                # Dapet! Lempar ke aria2c
                file_name = self._extract_filename_from_url_or_header(final_url)
                return self._download_file_with_aria2c([final_url], file_name)
            else:
                # Masih berupa halaman HTML, masuk (rekursif) ke dalam
                self._edit_telegram_message("🔄 Link mengarah ke halaman baru. Menyelam lebih dalam...")
                return self.smart_clicker(final_url, depth + 1)

        raise Exception("Smart Clicker gagal menemukan tombol atau link download yang valid.")

    def _process_sourceforge_download(self):
        """Menangani SourceForge secara khusus menggunakan Mirror Resolver"""
        def source_url(download_url):
            parsed_url = urlparse(download_url)
            path_parts = parsed_url.path.split('/')
            project_name = path_parts[2]
            file_path = '/'.join(path_parts[4:-1])
            query_params = {'projectname': project_name, 'filename': file_path}
            new_path = "/settings/mirror_choices"
            new_url_parts = (parsed_url.scheme, parsed_url.netloc, new_path, '', urlencode(query_params), '')
            return urlunparse(new_url_parts)
        
        def set_url(url, param_name, param_value):
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            query_params[param_name] = [param_value]
            new_query = urlencode(query_params, doseq=True)
            return urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, new_query, parsed_url.fragment))
        
        self.driver.get(self.url)
        
        download_button = WebDriverWait(self.driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#remaining-buttons > div.large-12 > a.button.green"))
        )
        aname = WebDriverWait(self.driver, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "#downloading > div.content > div.file-info > div"))
        ).text
        ahref = download_button.get_attribute('href')
        
        mirror_url = source_url(self.url)
        self.driver.get(mirror_url)
        
        list_items = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul#mirrorList > li"))
        )
        li_id = [item.get_attribute("id") for item in list_items]
        
        download_urls = [set_url(ahref, 'use_mirror', mirror_id) for mirror_id in li_id]
        
        self._edit_telegram_message(f"⬇️ **Memulai unduhan SourceForge dengan `aria2c`...**\nFile: `{aname}`")
        downloaded_filename = self._download_file_with_aria2c(download_urls, aname)
        
        if downloaded_filename:
            self._edit_telegram_message(f"✅ **SourceForge: Unduhan selesai!**\nFile: `{downloaded_filename}`\n\n**➡️ Mulai UPLOADING...**")
        
        return downloaded_filename

    # =========================================================
    # --- 4. MAIN ORCHESTRATOR (run) ---
    # =========================================================

    def run(self):
        """Titik masuk utama. Mengarahkan URL ke logika yang tepat."""
        self._send_telegram_message(f"⏳ **Menganalisis URL...**\nURL: `{self.url}`")
        downloaded_filename = None
        
        try:
            # 1. Pengecualian Khusus: MEGA
            if "mega.nz" in self.url:
                downloaded_filename = self._download_file_with_megatools(self.url)
                
            else:
                # 2. Sisanya Wajib Menggunakan Selenium
                if not self._initialize_selenium_driver(): 
                    raise Exception("Gagal inisialisasi driver Selenium.")
                
                # 3. Pengecualian Khusus: SourceForge
                if "sourceforge.net" in self.url or "sourceforge.io" in self.url:
                    downloaded_filename = self._process_sourceforge_download()
                
                # 4. Universal Fallback: Mediafire, Gofile, ApkAdmin, Pixeldrain, dll.
                else:
                    downloaded_filename = self.smart_clicker(self.url)

            if downloaded_filename:
                return downloaded_filename
            
        except Exception as e:
            print(f"❌ Unduhan utama gagal: {e}")
            self._edit_telegram_message(f"❌ **Unduhan GAGAL!**\nDetail: {str(e)[:150]}...")
            return None
            
        finally:
            # Cleanup otomatis ditangani oleh __del__
            pass
