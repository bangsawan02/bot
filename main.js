const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');
const { URL } = require('url');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadWgetNinja(targetUrl) {
    try {
        console.log(`🥷 Wget Ninja Analysis: ${targetUrl}`);

        // 1. Parsing URL
        const cleanUrl = targetUrl.split('?')[0].replace(/\/download$/, '');
        const urlObj = new URL(cleanUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');
        
        const projectName = pathParts[1];
        const filePath = pathParts.slice(3).join('/');
        const fileName = pathParts[pathParts.length - 1];

        // 2. Ambil Mirror List (Sesuai dokumentasi "Specifying a mirror")
        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(`🔎 Finding mirrors: ${mirrorPageUrl}`);

        const fetchHtmlCmd = `curl -L -s -A "${USER_AGENT}" "${mirrorPageUrl}"`;
        const html = execSync(fetchHtmlCmd).toString();
        const $ = cheerio.load(html);
        
        const mirrorIds = [];
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            throw new Error("Mirror tidak ditemukan. Cek koneksi atau IP.");
        }

        const selectedMirror = mirrorIds[0];
        console.log(`✅ Selected Mirror: ${selectedMirror}`);

        // 3. Susun URL Download sesuai dokumentasi Ninja
        // Format: [URL]/download?use_mirror=[ID]
        const ninjaDownloadUrl = `${cleanUrl}/download?use_mirror=${selectedMirror}`;

        console.log(`🚀 Wget Launching: ${fileName}`);

        // 4. Jalankan Wget dengan parameter sakti:
        // --user-agent: Menyamar jadi browser
        // --content-disposition: Mengambil nama file asli dari header server
        // --trust-server-names: Menghindari file tersimpan dengan nama 'download'
        // -O: Nama output file
        const wgetArgs = [
            `--user-agent=${USER_AGENT}`,
            '--header=Referer: https://sourceforge.net/',
            '--content-disposition',
            '--trust-server-names',
            '--quiet',              // Agar log tidak terlalu penuh, tapi tetap ada progress
            '--show-progress',      // Tampilkan progress bar
            '--no-check-certificate',
            '-O', fileName,         // Simpan dengan nama file yang sudah diparsing
            ninjaDownloadUrl
        ];

        const wgetProcess = spawn('wget', wgetArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        wgetProcess.on('close', (code) => {
            if (code === 0) {
                // Validasi ukuran (Ninja check)
                const stats = fs.statSync(fileName);
                const sizeMB = stats.size / (1024 * 1024);

                if (sizeMB < 10) {
                    console.error(`\n💀 Error: File cuma ${sizeMB.toFixed(2)}MB. Ini HTML, bukan ISO!`);
                    fs.unlinkSync(fileName);
                    process.exit(1);
                }

                console.log(`\n✨ Mission Accomplished: ${fileName} (${sizeMB.toFixed(2)} MB)`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
                process.exit(0);
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

const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadWgetNinja(PAYLOAD_URL);
} else {
    console.error("No URL provided.");
    process.exit(1);
}
