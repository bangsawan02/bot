import os
import subprocess
import requests
import time
import re
import tempfile
import shutil
import math
import sys
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

# --- PLAYWRIGHT IMPORTS ---
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_sync

# =========================================================
# CLASS UTAMA: DownloaderBot
# =========================================================

class DownloaderBot:
    """
    Mengelola seluruh proses download dari berbagai sumber, termasuk
    interaksi Playwright Browser dan integrasi Aria2c/Megatools.
    """
    
    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_download_dir = tempfile.mkdtemp()
        self.initial_message_id = None
        
        # State Playwright
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
    def __del__(self):
        self._close_browser()
        shutil.rmtree(self.temp_download_dir, ignore_errors=True)

    def _close_browser(self):
        """Memastikan semua instance Playwright ditutup dengan bersih."""
        try:
            if self.context: self.context.close()
            if self.browser: self.browser.close()
            if self.playwright: self.playwright.stop()
        except:
            pass

    # =========================================================
    # --- 1. METODE BANTUAN TELEGRAM & UMUM ---
    # (Metode ini SAMA PERSIS dengan kode aslimu, tidak diubah)
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
            self.initial_message_id = response.json().get('result', {}).get('message_id')
            return self.initial_message_id
        except Exception as e:
            return None
            
    def _edit_telegram_message(self, message_text):
        if not self.bot_token or not self.owner_id or not self.initial_message_id: return
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload = {"chat_id": self.owner_id, "message_id": self.initial_message_id, "text": message_text, "parse_mode": "Markdown"}
        try: requests.post(url, json=payload, timeout=10)
        except: pass 

    def _get_total_file_size_safe(self, url):
        try:
            response = requests.head(url, allow_redirects=True, timeout=10)
            content_length = response.headers.get('Content-Length')
            if content_length: return int(content_length)
        except requests.exceptions.RequestException: pass 
        
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                if 'Content-Length' in r.headers:
                    return int(r.headers['Content-Length'])
        except requests.exceptions.RequestException: pass
        return None

    def _extract_filename_from_url_or_header(self, download_url):
        file_name = None
        try:
            head_response = requests.head(download_url, allow_redirects=True, timeout=10)
            cd_header = head_response.headers.get('Content-Disposition')
            if cd_header:
                fname_match = re.search(r'filename\*?=["\']?(?:utf-8\'\')?([^"\';]+)["\']?', cd_header, re.I)
                if fname_match:
                    file_name = re.sub(r'[^\x00-\x7F]+', '', fname_match.group(1).strip())
            
            if not file_name:
                file_name = urlparse(download_url).path.split('/')[-1]
        except:
            file_name = urlparse(download_url).path.split('/')[-1]
            
        return file_name if file_name else "unknown_file"

    # =========================================================
    # --- 2. METODE DOWNLOAD INTI (ARIA2C & MEGATOOLS) ---
    # (Dipersingkat sedikit bagian try-except, logika utama tetap)
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
            last_notified_percent = 0
            
            while time.time() - start_time < 300:
                if os.path.exists(output_filename):
                    current_size = os.path.getsize(output_filename)
                    if total_size is not None and total_size > 0:
                        percent_now = int(current_size * 100 // total_size)
                        if (percent_now >= 50 and last_notified_percent < 50) or percent_now >= 100:
                            self._edit_telegram_message(f"⬇️ Download `{output_filename}` — {percent_now}% ({self._human_readable_size(current_size)}/{self._human_readable_size(total_size)})")
                            last_notified_percent = percent_now
                            
                    if total_size is not None and current_size >= total_size:
                        if process.poll() is None: process.kill()
                        return output_filename
                        
                if process.poll() is not None:
                    if os.path.exists(output_filename) and os.path.getsize(output_filename) > 0:
                        self._edit_telegram_message(f"✅ Download Selesai. `{output_filename}`")
                        return output_filename
                    return None
                time.sleep(3)
                
            if process and process.poll() is None: process.kill()
        except Exception:
            if process and process.poll() is None: process.kill()
        return None

    def _download_file_with_megatools(self, url):
        # ... (Logika megatools tidak berubah dari kodemu, biarkan utuh jika dipakai)
        pass # Disembunyikan agar script fokus ke Playwright, silahkan copy dari kodemu.

    # =========================================================
    # --- 3. METODE PLAYWRIGHT ---
    # =========================================================

    def _initialize_playwright_driver(self):
        """
        Menggantikan Selenium. Menggunakan Playwright dengan Stealth.
        """
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=True, # Ubah jadi False jika ingin lihat visual saat debug
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
            )
            self.context = self.browser.new_context(
                accept_downloads=True, # Native playwright download support
                viewport={"width": 1280, "height": 800}
            )
            self.page = self.context.new_page()
            
            # Terapkan Stealth anti-bot
            stealth_sync(self.page)
            
            self.page.set_default_timeout(60000)
            return True
        except Exception as e:
            print(f"❌ Gagal inisialisasi Playwright: {e}")
            return False

    def _process_browser_download(self):
        """Menangani Mediafire, Gofile, dan AGGRESIVE CLICKING."""
        self.page.goto(self.url)
        self._edit_telegram_message(f"⬇️ **[Mode Download]** Menganalisis situs...")

        # --- LOGIKA KHUSUS MEDIAFIRE ---
        if "mediafire" in self.url:
            self._edit_telegram_message("⬇️ **[MediaFire Mode]** Mengekstrak URL Download...")
            try:
                # Mediafire seringkali tidak butuh submit form, cukup ambil href
                dl_button = self.page.locator("#downloadButton")
                dl_button.wait_for(state="attached", timeout=20000)
                final_download_url = dl_button.get_attribute('href')
                
                file_name = self._extract_filename_from_url_or_header(final_download_url)
                self._edit_telegram_message(f"⬇️ **Memulai unduhan dengan `aria2c`...**\nFile: `{file_name}`")
                return self._download_file_with_aria2c([final_download_url], file_name)
            except Exception as e:
                raise Exception(f"Gagal ekstrak link Mediafire: {e}")

        # --- LOGIKA GOFILE / AGGRESIF (Mengandalkan Native Browser Download) ---
        action_performed = False
        download_info = None
        
        if "gofile" in self.url:
            self._edit_telegram_message("⬇️ **[Gofile Mode]** Mengklik tombol download...")
            try:
                # expect_download akan mendengarkan event download di latar belakang
                with self.page.expect_download(timeout=60000) as d_info:
                    self.page.locator("#download-btn").click(force=True)
                download_info = d_info.value
                action_performed = True
            except PlaywrightTimeout:
                print("Peringatan: Gagal Gofile, mencoba fallback agresif.")

        # Fallback Agresif
        if not action_performed:
            self._edit_telegram_message(f"⬇️ **[Mode Agresif]** Mencari dan mengklik tombol...")
            aggressive_selectors = [
                "//a[contains(translate(text(), 'DOWNLOAD', 'download'), 'download')]",
                "button:has-text('Download'), a[href*='download']",
                "button[type='submit']",
                "form input[type='submit']"
            ]
            
            for selector in aggressive_selectors:
                try:
                    target = self.page.locator(selector).first
                    if target.is_visible():
                        with self.page.expect_download(timeout=30000) as d_info:
                            target.click(force=True)
                        download_info = d_info.value
                        action_performed = True
                        break
                except Exception:
                    continue

        if action_performed and download_info:
            file_name = download_info.suggested_filename
            self._edit_telegram_message(f"⬇️ **[Browser]** Mengunduh `{file_name}`...")
            
            # Save dari temp browser ke current directory
            final_path = os.path.join(os.getcwd(), file_name)
            download_info.save_as(final_path)
            
            file_size = os.path.getsize(final_path)
            self._edit_telegram_message(f"✅ **Unduhan selesai!**\nFile: `{file_name}` ({self._human_readable_size(file_size)})")
            return file_name
        else:
            raise FileNotFoundError("Gagal memicu dan menangkap proses download di browser.")


    def _process_sourceforge_download(self):
        """Menangani SourceForge: Mendapatkan mirror URL."""
        def source_url(download_url):
            parsed = urlparse(download_url)
            parts = parsed.path.split('/')
            project, f_path = parts[2], '/'.join(parts[4:-1])
            query = urlencode({'projectname': project, 'filename': f_path})
            return urlunparse((parsed.scheme, parsed.netloc, "/settings/mirror_choices", '', query, ''))

        def set_url(url, param_name, param_value):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            query[param_name] = [param_value]
            new_query = urlencode(query, doseq=True)
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        
        self.page.goto(self.url)
        dl_button = self.page.locator("#remaining-buttons > div.large-12 > a.button.green")
        dl_button.wait_for(state="attached")
        ahref = dl_button.get_attribute('href')
        
        aname = self.page.locator("#downloading > div.content > div.file-info > div").text_content().strip()
        
        mirror_url = source_url(self.url)
        self.page.goto(mirror_url)
        
        # Ekstrak semua ID mirror
        list_items = self.page.locator("ul#mirrorList > li").all()
        li_id = [item.get_attribute("id") for item in list_items if item.get_attribute("id")]
        
        download_urls = [set_url(ahref, 'use_mirror', mirror_id) for mirror_id in li_id]
        
        self._edit_telegram_message(f"⬇️ **Memulai unduhan `aria2c`...**\nFile: `{aname}`")
        downloaded = self._download_file_with_aria2c(download_urls, aname)
        if downloaded:
            self._edit_telegram_message(f"✅ **SourceForge: Unduhan selesai!**\nFile: `{downloaded}`")
        return downloaded

    def _process_apkadmin_download(self):
        """
        Menangani Apk Admin dengan interceptor Playwright `page.on("response")`.
        Jauh lebih bersih dari CDP parsing Selenium.
        """
        self.page.goto(self.url)
        self._edit_telegram_message("⬇️ **[Apk Admin Mode]** Mencari dan mengirimkan FORM Step 1...")
        
        final_download_url = None
        
        # Event Listener untuk menangkap URL APK/ZIP
        def handle_response(response):
            nonlocal final_download_url
            if response.status == 200 and "apkadmin" not in response.url:
                if re.search(r'\.(apk|zip)$', response.url, re.IGNORECASE):
                    final_download_url = response.url

        # Pasang listener
        self.page.on("response", handle_response)
        
        # Submit form pertama
        try:
            self.page.locator("form[name='F1']").evaluate("form => form.submit()")
        except Exception:
            raise Exception("Gagal menemukan FORM 'F1'")

        self._edit_telegram_message("🔍 **[Apk Admin Mode]** Menunggu request jaringan (.apk/.zip)...")
        
        # Tunggu sampai URL didapat oleh listener (maksimal 15 detik)
        start_time = time.time()
        while time.time() - start_time < 15:
            if final_download_url:
                break
            time.sleep(1)
            
        # Lepas listener agar tidak memory leak
        self.page.remove_listener("response", handle_response)
        
        if not final_download_url:
            raise FileNotFoundError("Tidak ada URL download (.apk/.zip) yang terdeteksi di jaringan.")
            
        self._edit_telegram_message(f"🔍 Ditemukan URL:\n`{final_download_url}`")
        file_name = self._extract_filename_from_url_or_header(final_download_url)
        return self._download_file_with_aria2c([final_download_url], file_name)

    # =========================================================
    # --- 4. MAIN ORCHESTRATOR (run) ---
    # =========================================================

    def run(self):
        self._send_telegram_message(f"⏳ **Menganalisis URL...**\nURL: `{self.url}`")
        downloaded_filename = None
        
        try:
            if "mega.nz" in self.url:
                pass # Panggil fungsi _download_file_with_megatools yang lu punya
                
            elif "pixeldrain" in self.url:
                file_id_match = re.search(r'pixeldrain\.com/(u|l|f)/([a-zA-Z0-9]+)', self.url)
                if not file_id_match: raise ValueError("URL Pixeldrain tidak valid.")
                file_id = file_id_match.group(2)
                
                info_resp = requests.get(f"https://pixeldrain.com/api/file/{file_id}/info", timeout=10)
                file_info = info_resp.json()
                filename = file_info.get('name', f"px_{file_id}")
                dl_url = f"https://pixeldrain.com/api/file/{file_id}?download"
                
                downloaded_filename = self._download_file_with_aria2c([dl_url], filename)
                
            elif any(x in self.url for x in ["sourceforge", "gofile", "mediafire", "apkadmin", "http"]):
                # Gunakan PLAYWRIGHT
                if not self._initialize_playwright_driver(): 
                    raise Exception("Gagal inisialisasi driver Playwright.")
                
                if "sourceforge" in self.url:
                    downloaded_filename = self._process_sourceforge_download()
                elif "apkadmin" in self.url:
                    downloaded_filename = self._process_apkadmin_download()
                else:
                    downloaded_filename = self._process_browser_download()
            else:
                raise ValueError("URL tidak dikenali atau tidak didukung.")

            return downloaded_filename
            
        except Exception as e:
            self._edit_telegram_message(f"❌ **Unduhan GAGAL!**\nDetail: {str(e)[:150]}...")
            return None
        finally:
            self._close_browser()

# Tester
if __name__ == "__main__":
    if len(sys.argv) > 1:
        bot = DownloaderBot(sys.argv[1])
        bot.run()
