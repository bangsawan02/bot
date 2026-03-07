const { spawn } = require('child_process');
const fs = require('fs');
const { URL } = require('url');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadPureNinja(targetUrl) {
    try {
        console.log(`🥷 Pure Ninja Mission: ${targetUrl}`);

        // 1. Pastikan URL berakhir dengan /download agar trigger redirect mirror
        let ninjaUrl = targetUrl.split('?')[0];
        if (!ninjaUrl.endsWith('/download')) {
            ninjaUrl = ninjaUrl.replace(/\/$/, '') + '/download';
        }

        // 2. Ambil nama file dari URL untuk fallback
        const urlObj = new URL(ninjaUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');
        // Nama file biasanya ada di sebelum kata 'download'
        const fileName = pathParts[pathParts.length - 2] || 'downloaded_file.iso';

        console.log(`🚀 Wget Launching (Auto-Mirror): ${fileName}`);

        // 3. Jalankan Wget sesuai dokumentasi resmi Ninja:
        // --user-agent: Menyamar jadi browser (Wajib biar gak 403)
        // --content-disposition: Ambil nama file asli dari server mirror
        // --trust-server-names: Ikuti nama file setelah redirect (Sangat penting!)
        // -L / --location: (Wget otomatis follow redirect)
        const wgetArgs = [
            `--user-agent=${USER_AGENT}`,
            '--header=Referer: https://sourceforge.net/',
            '--content-disposition',
            '--trust-server-names',
            '--no-check-certificate',
            '--continue',             // Bisa resume kalau putus tengah jalan
            '--tries=10',             // Coba lagi kalau gagal konek
            '--show-progress',        // Biar kelihatan di log GitHub
            '-O', fileName,           // Simpan sesuai nama file di URL
            ninjaUrl
        ];

        const wgetProcess = spawn('wget', wgetArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        wgetProcess.on('close', (code) => {
            if (code === 0) {
                // Validasi ukuran (Pastikan bukan download halaman HTML)
                if (fs.existsSync(fileName)) {
                    const stats = fs.statSync(fileName);
                    const sizeMB = stats.size / (1024 * 1024);

                    if (sizeMB < 5) {
                        console.error(`\n💀 Error: File terlalu kecil (${sizeMB.toFixed(2)}MB). Kemungkinan besar kena blokir atau dapet HTML.`);
                        process.exit(1);
                    }

                    console.log(`\n✨ Mission Accomplished: ${fileName} (${sizeMB.toFixed(2)} MB)`);
                    fs.writeFileSync('downloaded_filename.txt', fileName);
                    process.exit(0);
                } else {
                    console.error("\n💀 Error: File tidak ditemukan setelah download.");
                    process.exit(1);
                }
            } else {
                console.error(`\n❌ Wget failed with exit code: ${code}`);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error(`\n💀 Error: ${error.message}`);
        process.exit(1);
    }
}

// Ambil dari PAYLOAD_URL yang dikirim workflow
const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadPureNinja(PAYLOAD_URL);
} else {
    console.error("Error: PAYLOAD_URL not found.");
    process.exit(1);
}
