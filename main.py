import os
import sys

# ✅ Import hanya Class DownloaderBot dari file utils
from utils import DownloaderBot

# Dapatkan URL dari environment variable
url_to_download = os.environ.get("MEDIAFIRE_PAGE_URL")

if __name__ == "__main__":
    if url_to_download:
        print(f"Memulai proses download untuk URL: {url_to_download}")
        downloaded_filename = None
        
        try:
            # 1. Inisialisasi Class
            downloader = DownloaderBot(url_to_download)
            
            # 2. Jalankan Proses Utama dan tangkap nama file yang diunduh
            downloaded_filename = downloader.run()
            
            # 3. Buat downloaded_filename.txt jika berhasil
            if downloaded_filename:
                with open("downloaded_filename.txt", "w") as f: 
                    f.write(downloaded_filename)
                print(f"✅ Selesai. Nama file: {downloaded_filename} telah dicatat dalam downloaded_filename.txt")
            else:
                print("❌ Proses download selesai tanpa menghasilkan file yang valid.")
                sys.exit(1)

        except Exception as e:
            # Fatal error di luar logika download
            print(f"Error fatal saat eksekusi utama: {e}")
            sys.exit(1)
    else:
        print("Error: MEDIAFIRE_PAGE_URL environment variable not set.")
        sys.exit(1)
