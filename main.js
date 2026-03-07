const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');
const { URL } = require('url');

// Identitas Browser Terpercaya
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadNinjaSF(targetUrl) {
    try {
        console.log(`🥷 Ninja Analysis: ${targetUrl}`);

        // 1. Parsing URL untuk mendapatkan komponen path
        const cleanUrl = targetUrl.split('?')[0].replace(/\/download$/, '');
        const urlObj = new URL(cleanUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');
        
        // Struktur: /projects/[project]/files/[subfolders]/[file]
        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');

        if (projectIndex === -1 || filesIndex === -1) {
            throw new Error("URL tidak valid (Missing projects/files path)");
        }

        const projectName = pathParts[projectIndex + 1];
        const filePath = pathParts.slice(filesIndex + 1).join('/');
        const fileName = pathParts[pathParts.length - 1];

        // 2. Ambil List Mirror (Halaman Pilihan)
        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(`🔎 Scraping Mirrors: ${mirrorPageUrl}`);

        const fetchHtmlCmd = `curl -L -s -A "${USER_AGENT}" -H "Referer: https://sourceforge.net/" "${mirrorPageUrl}"`;
        const html = execSync(fetchHtmlCmd).toString();
        const $ = cheerio.load(html);
        
        const mirrorIds = [];
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            throw new Error("Gagal mendapatkan mirror. IP GitHub mungkin diblokir sementara.");
        }

        // Ambil mirror pertama
        const selectedMirror = mirrorIds[1];
        
        // 3. Susun DIRECT URL (Melewati halaman 'Thank You' HTML)
        // Format: https://[MIRROR].dl.sourceforge.net/project/[PROJECT]/[FILE_PATH]
        const directMirrorUrl = `https://${selectedMirror}.dl.sourceforge.net/project/${projectName}/${filePath}`;

        console.log(`✅ Selected Mirror: ${selectedMirror}`);
        console.log(`🚀 Ninja Attack (Direct)! Downloading: ${fileName}`);

        // 4. Jalankan CURL dengan Header Anti-Bot
        const curlArgs = [
            '-L',                    // Follow redirect jika ada
            '-A', USER_AGENT, 
            '-H', `Referer: https://sourceforge.net/projects/${projectName}/files/`,
            '--retry', '5',
            '--connect-timeout', '30',
            '-o', fileName,          // Simpan sebagai file ISO
            directMirrorUrl          // Langsung tembak ke file server
        ];

        // Pakai 'inherit' agar Progress Bar (Total Size) terlihat di GitHub Actions
        const ninjaDownload = spawn('curl', curlArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        ninjaDownload.on('close', (code) => {
            if (code === 0) {
                // Cek ukuran file setelah download selesai (Minimal harus > 10MB)
                const stats = fs.statSync(fileName);
                const fileSizeInBytes = stats.size;
                const fileSizeInMB = fileSizeInBytes / (1024 * 1024);

                if (fileSizeInMB < 10) {
                    console.error(`\n💀 ERROR: File terlalu kecil (${fileSizeInMB.toFixed(2)} MB). Sepertinya terdownload HTML, bukan ISO.`);
                    fs.unlinkSync(fileName); // Hapus file sampah
                    process.exit(1);
                }

                console.log(`\n✨ Mission Accomplished: ${fileName} (${fileSizeInMB.toFixed(2)} MB)`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
                process.exit(0);
            } else {
                console.error(`\n❌ Ninja Failed. Exit Code: ${code}`);
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
    downloadNinjaSF(PAYLOAD_URL);
} else {
    console.error("No PAYLOAD_URL provided.");
    process.exit(1);
}
