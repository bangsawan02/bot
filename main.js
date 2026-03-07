const { execSync, spawn } = require('child_process');
const fs = require('fs');
const { URL } = require('url');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

async function downloadBypassNinja(targetUrl) {
    try {
        console.log(`🥷 Anti-403 Ninja Mission: ${targetUrl}`);

        let ninjaUrl = targetUrl.split('?')[0];
        if (!ninjaUrl.endsWith('/download')) {
            ninjaUrl = ninjaUrl.replace(/\/$/, '') + '/download';
        }

        // 1. Ambil Cookie dan URL Redirect Final menggunakan CURL (Silent)
        // Kita butuh ini agar SourceForge mengira kita sudah melewati halaman "Waiting..."
        console.log("🍪 Harvesting cookies and resolving redirect...");
        const cookieFile = 'sf-cookies.txt';
        
        // Perintah ini akan menyimpan cookie ke file
        execSync(`curl -L -c ${cookieFile} -A "${USER_AGENT}" -s -o /dev/null "${ninjaUrl}"`);

        // 2. Tentukan nama file dari URL
        const urlObj = new URL(ninjaUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');
        const fileName = pathParts[pathParts.length - 2] || 'file.iso';

        console.log(`🚀 Wget Launching with Cookies: ${fileName}`);

        // 3. Jalankan Wget dengan Cookie yang sudah dipanen
        const wgetArgs = [
            `--user-agent=${USER_AGENT}`,
            `--load-cookies=${cookieFile}`,  // PENTING: Gunakan cookie hasil pancingan
            '--header=Referer: https://sourceforge.net/',
            '--content-disposition',
            '--trust-server-names',
            '--no-check-certificate',
            '--continue',
            '--show-progress',
            '-O', fileName,
            ninjaUrl
        ];

        const wgetProcess = spawn('wget', wgetArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        wgetProcess.on('close', (code) => {
            // Hapus file cookie setelah selesai
            if (fs.existsSync(cookieFile)) fs.unlinkSync(cookieFile);

            if (code === 0) {
                const stats = fs.statSync(fileName);
                const sizeMB = stats.size / (1024 * 1024);

                if (sizeMB < 10) {
                    console.error(`\n💀 Error: File cuma ${sizeMB.toFixed(2)}MB. Masih kena 403 atau HTML.`);
                    process.exit(1);
                }

                console.log(`\n✨ Mission Accomplished: ${fileName} (${sizeMB.toFixed(2)} MB)`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
                process.exit(0);
            } else {
                console.error(`\n❌ Wget failed code: ${code}`);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error(`\n💀 Error: ${error.message}`);
        process.exit(1);
    }
}

const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadBypassNinja(PAYLOAD_URL);
} else {
    process.exit(1);
}
