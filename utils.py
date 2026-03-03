import os
import subprocess
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
#import undetected_chromedriver as uc # PENTING!
from webdriver_manager.chrome import ChromeDriverManager # <-- Diperlukan lagi
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
    Mengelola seluruh proses download dari berbagai sumber, termasuk
    interaksi Selenium/Headless Browser dan integrasi Aria2c/Megatools.
    """
    
    def __init__(self, url):
        # --- KONFIGURASI DAN STATE ---
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        # Tentukan directory untuk download sementara
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        self.driver = None
        
    def __del__(self):
        # Pastikan driver dihentikan dan folder temp dihapus saat objek dihancurkan
        if self.driver:
            self.driver.quit()
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
        """Mengirim pesan dan menyimpan message_id ke self.initial_message_id."""
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
        """Mengedit pesan yang sudah ada (menggunakan self.initial_message_id)."""
        if not self.bot_token or not self.owner_id or not self.initial_message_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, 
                   "text": message_text, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            pass 

    def _get_total_file_size_safe(self, url):
        """Mendapatkan ukuran file total dari URL dengan aman."""
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
        """Mendapatkan nama file dari header Content-Disposition atau fallback ke path URL."""
        file_name = None
        try:
            head_response = requests.head(download_url, allow_redirects=True, timeout=10)
            head_response.raise_for_status()
            
            cd_header = head_response.headers.get('Content-Disposition')
            if cd_header:
                # Mencari filename* atau filename sederhana
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
        """Mengunduh file menggunakan aria2c dengan progress update."""
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
        """Mengunduh file dari MEGA dengan megatools."""
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
    # --- 3. METODE SELENIUM ---
    # =========================================================

    def _initialize_selenium_driver(self):
        """
        Menginisialisasi dan mengkonfigurasi Chrome Driver (Headless) 
        menggunakan selenium-stealth untuk menghindari deteksi bot.
        Mengaktifkan Performance Logging untuk CDP Network Events.
        """
        
        chrome_prefs = {
            "download.default_directory": self.temp_download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        
        options = webdriver.ChromeOptions()
        
        # Tambahkan preferensi untuk download
        options.add_experimental_option("prefs", chrome_prefs)
        
        # Opsi Headless dan anti-sandbox
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled') # Tambahan stealth

        # AKTIFKAN PERFORMANCE LOGGING (CDP)
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'}) 
        
        try:
            # 1. Inisialisasi Driver Standar
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            
            # 2. Terapkan Lapisan Stealth (PENTING!)
            stealth(self.driver,
                    languages=["en-US", "en"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True,
                    )
            
            self.driver.set_page_load_timeout(60) 
            
            return True
        except Exception as e:
            print(f"❌ Gagal inisialisasi Selenium Driver: {e}")
            return False
    def _process_selenium_download(self):
        """
        Menangani Gofile, Mediafire, dan AGGRESIVE CLICKING.
        (Logika MediaFire, Gofile, dan Aggressive Clicking)
        """
        driver = self.driver
        url = self.url
        downloaded_filename = None
        
        driver.get(url)
        self._edit_telegram_message(f"⬇️ **[Mode Download]** Menganalisis situs...")

        # --- LOGIKA KHUSUS MEDIAFIRE ---
        if "mediafire" in url:
            FORM_SELECTOR_STEP_1 = "form.dl-btn-form" 
            SELECTOR_STEP_2 = "#downloadButton"

            self._edit_telegram_message("⬇️ **[MediaFire Mode]** Mencari dan mengirimkan FORM Step 1...")
            try:
                form_element = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, FORM_SELECTOR_STEP_1))
                )
                form_element.submit()
            except TimeoutException:
                raise TimeoutException(f"Gagal menemukan FORM MediaFire '{FORM_SELECTOR_STEP_1}'.")

            self._edit_telegram_message("🔍 **[MediaFire Mode]** Halaman kedua dimuat. Mengekstrak URL Download Langsung...")
            
            try:
                download_button = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, SELECTOR_STEP_2))
                )
                final_download_url = download_button.get_attribute('href')
                if not final_download_url: raise Exception("Atribut 'href' pada tombol download kosong.")

                file_name = self._extract_filename_from_url_or_header(final_download_url)
                
                self._edit_telegram_message(f"⬇️ **Memulai unduhan dengan `aria2c`...**\nFile: `{file_name}`")
                downloaded_filename = self._download_file_with_aria2c([final_download_url], file_name)
                
                if downloaded_filename:
                    self._edit_telegram_message(f"✅ **MediaFire: Unduhan selesai!**\nFile: `{downloaded_filename}`\n\n**➡️ Mulai UPLOADING...**")
                    return downloaded_filename
                else:
                    raise Exception("Aria2c gagal mengunduh file.")
            except Exception as e:
                raise Exception(f"Gagal saat ekstraksi link atau pemanggilan Aria2c: {e}")
            
        # --- LOGIKA GOFILE ATAU LOGIKA AGGRESIF UMUM ---
        # (Implementasi logika agresif di sini, untuk situs yang tidak spesifik)
        action_performed = False
        
        if "gofile" in url:
            SELECTOR_STEP_2 = "#download-btn"
            self._edit_telegram_message("⬇️ **[Gofile Mode]** Mencari dan mengklik tombol download...")
            try:
                download_button = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTOR_STEP_2))
                )
                driver.execute_script("arguments[0].click();", download_button)
                time.sleep(2)
                action_performed = True
            except TimeoutException:
                print(f"Peringatan: Gagal menemukan tombol Gofile. Mencoba mode Agresif.")
                pass
        
        # Mode Agresif / Fallback (Monitoring folder download oleh Selenium)
        if not action_performed and "mediafire" not in url:
            self._edit_telegram_message(f"⬇️ **[Mode Agresif]** Mencari dan mengklik tombol download...")
            
            aggressive_selectors = [
                (By.XPATH, "//a[contains(translate(text(), 'DOWNLOAD', 'download'), 'download') or contains(translate(text(), 'GET', 'get'), 'get')]"),
                (By.CSS_SELECTOR, "button:has-text('Download'), a[href*='download'], button[id*='download']"),
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, "form input[type='submit']"),
            ]
            
            for by, selector in aggressive_selectors:
                try:
                    element = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    driver.execute_script("arguments[0].click();", element)
                    time.sleep(3)
                    action_performed = True
                    break
                except TimeoutException:
                    continue
        
        # 4. Monitoring Download (Logika Monitoring Ketat)
        if action_performed:
            start_time = time.time()
            timeout = 300
            initial_files = set(os.listdir(self.temp_download_dir))
            
            while time.time() - start_time < timeout:
                current_files = os.listdir(self.temp_download_dir)
                
                is_downloading = any(fname.endswith(('.crdownload', '.tmp')) or "Unconfirmed" in fname for fname in current_files)
                
                final_files_list = [
                    f for f in current_files 
                    if not f.endswith(('.crdownload', '.tmp')) and 
                       not f.startswith('.') and 
                       "Unconfirmed" not in f and 
                       f not in initial_files
                ]

                if not is_downloading and final_files_list:
                    break
                
                time.sleep(1)
                
            else:
                raise TimeoutException("Unduhan gagal atau melebihi batas waktu 300 detik.")

            # 5. Finalisasi File
            final_files_list = [
                f for f in os.listdir(self.temp_download_dir) 
                if not f.endswith(('.crdownload', '.tmp')) and not f.startswith('.') and "Unconfirmed" not in f
            ]
            
            if final_files_list:
                latest_file_path = max([os.path.join(self.temp_download_dir, f) for f in final_files_list], key=os.path.getctime)
                downloaded_filename = os.path.basename(latest_file_path)
                
                shutil.move(latest_file_path, os.path.join(os.getcwd(), downloaded_filename))
                
                file_size = os.path.getsize(downloaded_filename)
                self._edit_telegram_message(f"✅ **Unduhan selesai!**\nFile: `{downloaded_filename}` ({self._human_readable_size(file_size)})\n\n**➡️ Mulai UPLOADING...**")
                
                return downloaded_filename
            else:
                raise FileNotFoundError("Gagal menemukan file yang diunduh setelah monitoring.")
        
        return downloaded_filename


    def _process_sourceforge_download(self):
        """Menangani SourceForge: Mendapatkan mirror URL dan memanggil aria2c."""
        
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
        
        # Ekstraksi nama file dan link tombol pertama
        download_button = WebDriverWait(self.driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#remaining-buttons > div.large-12 > a.button.green"))
        )
        aname = WebDriverWait(self.driver, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "#downloading > div.content > div.file-info > div"))
        ).text
        ahref = download_button.get_attribute('href')
        
        # Navigasi ke halaman mirror
        mirror_url = source_url(self.url)
        self.driver.get(mirror_url)
        
        list_items = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul#mirrorList > li"))
        )
        li_id = [item.get_attribute("id") for item in list_items]
        
        download_urls = [set_url(ahref, 'use_mirror', mirror_id) for mirror_id in li_id]
        
        self._edit_telegram_message(f"⬇️ **Memulai unduhan dengan `aria2c`...**\nFile: `{aname}`")
        downloaded_filename = self._download_file_with_aria2c(download_urls, aname)
        
        if downloaded_filename:
            self._edit_telegram_message(f"✅ **SourceForge: Unduhan selesai!**\nFile: `{downloaded_filename}`\n\n**➡️ Mulai UPLOADING...**")
        
        return downloaded_filename


    def _process_apkadmin_download(self):
        """
        Menangani proses Apk Admin: submit form dan mengekstrak URL download 
        langsung dari log jaringan (CDP Network Logging) dengan memprioritaskan 
        URL yang berakhiran .apk atau .zip.
        
        ⚠️ DEBUG: Mencetak respons HTML halaman kedua ke konsol.
        """
        driver = self.driver
        driver.get(self.url)
        
        SELECTOR_FORM = "form[name='F1']"
        self._edit_telegram_message("⬇️ **[Apk Admin Mode]** Mencari dan mengirimkan FORM Step 1...")
        
        # 1. KLIK/SUBMIT FORM PERTAMA
        try:
            form_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTOR_FORM))
            )
            form_element.submit()
        except TimeoutException:
            raise TimeoutException(f"Gagal menemukan FORM '{SELECTOR_FORM}'.")

        # --- LANGKAH DEBUG: PRINT RESPONS HTML KE CONSOLE ---
        self._edit_telegram_message("🔍 **[Apk Admin Mode]** Halaman kedua dimuat. Mencetak respons HTML ke konsol...")
        
        # Beri waktu driver untuk memuat halaman baru
        time.sleep(5) 
        
        html_content = driver.page_source
        
            
        print("\n--- RESPONS HTML DARI SUBMIT FORM F1 ---")
        print(html_content)
        print("--- AKHIR RESPONS HTML ---")
        
        # --- 2. EKSTRAKSI URL DARI NETWORK LOGS (Logika dipertahankan, print log dihapus) ---
        self._edit_telegram_message("🔍 **[Apk Admin Mode]** Menganalisis log jaringan (CDP), mencari .apk/.zip...")
        
        # Beri waktu tambahan untuk menyelesaikan permintaan jaringan
        time.sleep(2) 
        
        final_download_url = None
        
        try:
            logs = driver.get_log('performance')
            network_requests = []
            
            # Regex untuk mencari URL yang mengandung ekstensi .apk atau .zip
            FILE_EXTENSION_REGEX = re.compile(r'\.(apk|zip)$', re.I)
            
            # Memproses dan memfilter log
            for entry in logs:
                log_json = json.loads(entry['message'])
                message = log_json.get('message')
                
                if message and message.get('method') == 'Network.responseReceived':
                    params = message.get('params')
                    response = params.get('response')
                    url = response.get('url')
                    status = response.get('status')
                    
                    # Logika filtering yang sebenarnya
                    is_download_candidate = (
                        status == 200 and
                        "apkadmin" not in url and 
                        FILE_EXTENSION_REGEX.search(url)
                    )
                    
                    if is_download_candidate:
                        content_length = response.get('headers', {}).get('Content-Length')
                        size = int(content_length) if content_length else 0
                        
                        network_requests.append({
                            'url': url,
                            'size': size
                        })
            
            # Urutkan berdasarkan ukuran file terbesar
            if network_requests:
                network_requests.sort(key=lambda x: x['size'], reverse=True)
                final_download_url = network_requests[0]['url']
                
                self._edit_telegram_message(f"🔍 **[Apk Admin Mode]** Ditemukan URL download (.apk/.zip) dari log jaringan:\n`{final_download_url}`")

            else:
                raise FileNotFoundError("Tidak ada URL download (.apk/.zip) yang terdeteksi di log jaringan.")

        except Exception as e:
            raise Exception(f"Gagal saat ekstraksi link dari Network Log: {e}")
        
        # 3. PANGGIL ARIA2C
        file_name = self._extract_filename_from_url_or_header(final_download_url)
        
        self._edit_telegram_message(f"⬇️ **Memulai unduhan dengan `aria2c`...**\nFile: `{file_name}`")
        downloaded_filename = self._download_file_with_aria2c([final_download_url], file_name)
        
        if downloaded_filename:
            self._edit_telegram_message(f"✅ **[Apk Admin Mode] Unduhan selesai!**\nFile: `{downloaded_filename}`\n\n**➡️ Mulai UPLOADING...**")
            return downloaded_filename
        else:
            raise Exception("Aria2c gagal mengunduh file.")
    # =========================================================
    # --- 4. MAIN ORCHESTRATOR (run) ---
    # =========================================================

    def run(self):
        """Titik masuk utama. Memproses URL dan mengarahkan ke handler yang tepat."""
        self._send_telegram_message(f"⏳ **Menganalisis URL...**\nURL: `{self.url}`")
        downloaded_filename = None
        
        try:
            # 1. LOGIKA UTAMA (MEGA, PIXELDRAIN)
            if "mega.nz" in self.url:
                downloaded_filename = self._download_file_with_megatools(self.url)
            
            elif "pixeldrain" in self.url:
                file_id_match = re.search(r'pixeldrain\.com/(u|l|f)/([a-zA-Z0-9]+)', self.url)
                if not file_id_match: raise ValueError("URL Pixeldrain tidak valid.")
                file_id = file_id_match.group(2)
                info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
                
                self._edit_telegram_message(f"🔍 **Mendapatkan informasi file dari Pixeldrain...** ID: `{file_id}`")
                
                info_resp = requests.get(info_url, timeout=10)
                info_resp.raise_for_status()
                file_info = info_resp.json()

                filename = file_info.get('name', f"pixeldrain_download_{file_id}")
                download_url = f"https://pixeldrain.com/api/file/{file_id}?download"
                
                self._edit_telegram_message(f"⬇️ **Memulai unduhan dengan `aria2c`...**\nFile: `{filename}`")
                downloaded_filename = self._download_file_with_aria2c([download_url], filename)
                
                if downloaded_filename:
                    self._edit_telegram_message(f"✅ **Pixeldrain: Unduhan selesai!**\nFile: `{downloaded_filename}`\n\n**➡️ Mulai UPLOADING...**")
            
            # 2. LOGIKA SELENIUM
            elif "sourceforge" in self.url or "gofile" in self.url or "mediafire" in self.url or "apkadmin" in self.url or "http" in self.url:
                
                if not self._initialize_selenium_driver(): 
                    raise Exception("Gagal inisialisasi driver Selenium.")
                
                if "sourceforge" in self.url:
                    downloaded_filename = self._process_sourceforge_download()
                elif "apkadmin" in self.url:
                     downloaded_filename = self._process_apkadmin_download()
                else:
                    downloaded_filename = self._process_selenium_download()
            
            else:
                raise ValueError("URL tidak dikenali atau tidak didukung.")

            if downloaded_filename:
                # Tulis file nama terakhir
                return downloaded_filename
            
        except Exception as e:
            print(f"❌ Unduhan utama gagal: {e}")
            self._edit_telegram_message(f"❌ **Unduhan GAGAL!**\nDetail: {str(e)[:150]}...")
            return None
            
        finally:
            # Cleanup otomatis oleh __del__
            pass
