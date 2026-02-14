import os
import sys
import asyncio

# Import class sesuai yang kamu pakai (DownloaderBot atau DownloaderBotAsync)
from utils import DownloaderBotAsync  # ganti ke DownloaderBot kalau bukan async

def main():
    url_to_download = os.environ.get("MEDIAFIRE_PAGE_URL")

    if not url_to_download:
        print("❌ Error: MEDIAFIRE_PAGE_URL environment variable not set.")
        sys.exit(1)

    print(f"Memulai proses download untuk URL: {url_to_download}")
    downloaded_filename = None

    try:
        # Inisialisasi class
        downloader = DownloaderBotAsync(url_to_download)

        # Jalankan proses utama (async)
        downloaded_filename = asyncio.run(downloader.run())

        # Buat file txt kalau berhasil
        if downloaded_filename:
            with open("downloaded_filename.txt", "w") as f:
                f.write(downloaded_filename)
            print(f"✅ Selesai. Nama file: {downloaded_filename} dicatat di downloaded_filename.txt")
        else:
            print("❌ Proses download selesai tanpa menghasilkan file valid.")
            sys.exit(1)

    except Exception as e:
        print(f"💥 Error fatal saat eksekusi utama: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
