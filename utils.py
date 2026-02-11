import os
import subprocess
import requests
import tempfile
import shutil
import time
import math
import re
import json
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


class DownloaderBot:

    # =====================================================
    # INIT / CLEANUP
    # =====================================================

    def __init__(self, url):
        self.url = url
        self.bot_token = os.environ.get("BOT_TOKEN")
        self.owner_id = os.environ.get("PAYLOAD_SENDER")
        self.temp_dir = tempfile.mkdtemp()
        self.driver = None
        self.initial_message_id = None

    def cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =====================================================
    # TELEGRAM
    # =====================================================

    def _send(self, text):
        if not self.bot_token or not self.owner_id:
            return
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.owner_id, "text": text},
                timeout=10
            )
            data = r.json()
            self.initial_message_id = data.get("result", {}).get("message_id")
        except:
            pass

    def _edit(self, text):
        if not self.initial_message_id:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/editMessageText",
                json={
                    "chat_id": self.owner_id,
                    "message_id": self.initial_message_id,
                    "text": text
                },
                timeout=10
            )
        except:
            pass

    # =====================================================
    # UTIL
    # =====================================================

    def _human(self, size):
        if not size:
            return "0B"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = int(math.floor(math.log(size, 1024)))
        return f"{round(size / (1024 ** i), 2)} {units[i]}"

    def _sanitize(self, name):
        name = os.path.basename(name)
        return re.sub(r'[<>:"/\\|?*]', "_", name) or "file"

    def _get_content_type(self, url):
        try:
            r = requests.head(url, allow_redirects=True, timeout=10)
            return r.headers.get("Content-Type", "")
        except:
            return ""

    # =====================================================
    # ARIA2 SINGLE
    # =====================================================

    def _aria2(self, url, filename):

        filename = self._sanitize(filename)

        cmd = [
            "aria2c",
            "--allow-overwrite=true",
            "--file-allocation=none",
            "-x", "16",
            "-s", "16",
            "-o", filename,
            url
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        while True:
            line = process.stdout.readline()
            if not line:
                break

            percent_match = re.search(r'(\d+)%', line)
            if percent_match:
                self._edit(f"⬇️ {filename} — {percent_match.group(1)}%")

        process.wait()

        if process.returncode == 0 and os.path.exists(filename):
            size = os.path.getsize(filename)
            self._edit(f"✅ Selesai: {filename} ({self._human(size)})")
            return filename

        raise Exception("aria2 gagal")

    # =====================================================
    # ARIA2 MULTI (SOURCEFORGE MIRROR)
    # =====================================================

    def _aria2_multi(self, urls, filename):

        filename = self._sanitize(filename)

        cmd = [
            "aria2c",
            "--allow-overwrite=true",
            "--file-allocation=none",
            "-x", "16",
            "-s", "16",
            "-o", filename
        ] + urls

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        while True:
            line = process.stdout.readline()
            if not line:
                break

            percent_match = re.search(r'(\d+)%', line)
            if percent_match:
                self._edit(f"⬇️ {filename} — {percent_match.group(1)}%")

        process.wait()

        if process.returncode == 0 and os.path.exists(filename):
            size = os.path.getsize(filename)
            self._edit(f"✅ SourceForge selesai: {filename} ({self._human(size)})")
            return filename

        raise Exception("aria2 mirror gagal")

    # =====================================================
    # MEGA
    # =====================================================

    def _mega(self):
        temp_dir = tempfile.mkdtemp()
        try:
            subprocess.check_call(["megatools", "dl", self.url], cwd=temp_dir)
            files = [f for f in os.listdir(temp_dir) if not f.endswith(".megatools")]
            if not files:
                raise Exception("File MEGA tidak ditemukan")
            src = os.path.join(temp_dir, files[0])
            dst = os.path.join(os.getcwd(), files[0])
            shutil.move(src, dst)
            self._edit(f"✅ MEGA selesai: {files[0]}")
            return files[0]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # =====================================================
    # PIXELDRAIN
    # =====================================================

    def _pixeldrain(self):
        match = re.search(r'pixeldrain\.com/(u|l|f)/([a-zA-Z0-9]+)', self.url)
        if not match:
            raise Exception("URL Pixeldrain tidak valid")

        file_id = match.group(2)
        info = requests.get(
            f"https://pixeldrain.com/api/file/{file_id}/info",
            timeout=10
        ).json()

        filename = info.get("name", f"{file_id}.bin")
        download_url = f"https://pixeldrain.com/api/file/{file_id}?download"

        return self._aria2(download_url, filename)

    # =====================================================
    # SELENIUM INIT
    # =====================================================

    def _init_driver(self, performance_log=False):

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        if performance_log:
            options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)

    # =====================================================
    # MEDIAFIRE
    # =====================================================

    def _mediafire(self):
        self.driver.get(self.url)

        form = WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "form.dl-btn-form"))
        )
        form.submit()

        btn = WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.ID, "downloadButton"))
        )

        link = btn.get_attribute("href")
        return self._aria2(link, link.split("/")[-1])

    # =====================================================
    # SOURCEFORGE (ORIGINAL MIRROR LOGIC)
    # =====================================================

    def _sourceforge(self):

        def build_mirror_url(download_url):
            parsed = urlparse(download_url)
            parts = parsed.path.split('/')
            project_name = parts[2]
            file_path = '/'.join(parts[4:-1])

            query = urlencode({
                "projectname": project_name,
                "filename": file_path
            })

            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                "/settings/mirror_choices",
                "",
                query,
                ""
            ))

        def set_mirror(url, mirror_id):
            parsed = urlparse(url)
            q = parse_qs(parsed.query)
            q["use_mirror"] = [mirror_id]
            return urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(q, doseq=True),
                parsed.fragment
            ))

        self.driver.get(self.url)

        download_button = WebDriverWait(self.driver, 20).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "#remaining-buttons > div.large-12 > a.button.green")
            )
        )

        first_link = download_button.get_attribute("href")
        file_name = download_button.text.strip() or "sourceforge_file"

        mirror_page = build_mirror_url(self.url)
        self.driver.get(mirror_page)

        mirror_items = WebDriverWait(self.driver, 20).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "ul#mirrorList > li")
            )
        )

        mirror_ids = [item.get_attribute("id") for item in mirror_items if item.get_attribute("id")]

        if not mirror_ids:
            raise Exception("Mirror tidak ditemukan")

        mirror_urls = [set_mirror(first_link, mid) for mid in mirror_ids]

        return self._aria2_multi(mirror_urls, file_name)

    # =====================================================
    # APKADMIN (CDP)
    # =====================================================

    def _apkadmin(self):

        self._init_driver(performance_log=True)
        self.driver.get(self.url)

        form = WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "form[name='F1']"))
        )
        form.submit()

        time.sleep(5)

        logs = self.driver.get_log("performance")

        for entry in logs:
            msg = json.loads(entry["message"])["message"]
            if msg["method"] == "Network.responseReceived":
                response = msg["params"]["response"]
                url = response["url"]
                if re.search(r'\.(apk|zip)$', url):
                    return self._aria2(url, url.split("/")[-1])

        raise Exception("Link APK tidak ditemukan")

    # =====================================================
    # GENERIC FALLBACK
    # =====================================================

    def _generic(self):

        self.driver.get(self.url)

        selectors = [
            (By.XPATH, "//a[contains(translate(text(),'DOWNLOAD','download'),'download')]"),
            (By.CSS_SELECTOR, "a[href*='download']")
        ]

        for by, sel in selectors:
            try:
                btn = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((by, sel))
                )
                link = btn.get_attribute("href")
                if link:
                    return self._aria2(link, link.split("/")[-1])
            except:
                continue

        raise Exception("Tombol download tidak ditemukan")

    # =====================================================
    # RUN
    # =====================================================

    def run(self):

        self._send(f"⏳ Memproses:\n{self.url}")

        try:

            content_type = self._get_content_type(self.url)
            if "application" in content_type or "octet-stream" in content_type:
                return self._aria2(self.url, self.url.split("/")[-1])

            if "mega.nz" in self.url:
                return self._mega()

            if "pixeldrain" in self.url:
                return self._pixeldrain()

            self._init_driver()

            if "mediafire" in self.url:
                return self._mediafire()

            if "sourceforge" in self.url:
                return self._sourceforge()

            if "apkadmin" in self.url:
                return self._apkadmin()

            return self._generic()

        except Exception as e:
            self._edit(f"❌ Error: {str(e)}")
            return None

        finally:
            self.cleanup()
