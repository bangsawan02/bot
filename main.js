const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');
const { URL } = require('url');

// Identitas "Ninja": Harus mirip browser asli agar tidak kena 403
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadNinjaSF(targetUrl) {
    try {
        console.log(`🥷 Ninja Analysis: ${targetUrl}`);

        // 1. Bersihkan URL dan ambil komponen penting
        const cleanUrl = targetUrl.split('?')[0].replace(/\/download$/, '');
        const urlObj = new URL(cleanUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');
        
        // Contoh path: /projects/blissos-x86/files/Official/.../file.iso
        const projectName = pathParts[1];
        const filePath = pathParts.slice(3).join('/');
        const fileName = pathParts[pathParts.length - 1];

        // 2. Ambil list mirror via CURL (Strategi Stealth)
        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(`🔎 Finding mirrors at: ${mirrorPageUrl}`);

        const fetchHtmlCmd = `curl -L -s -A "${USER_AGENT}" -H "Referer: https://sourceforge.net/" "${mirrorPageUrl}"`;
        const html = execSync(fetchHtmlCmd).toString();
        const $ = cheerio.load(html);
        
        const mirrorIds = [];
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            throw new Error("Gagal mendapatkan list mirror. SourceForge mungkin memblokir IP ini.");
        }

        // Pilih mirror pertama (Ninja style: manual selection)
        const selectedMirror = mirrorIds[0];
        console.log(`✅ Selected Mirror: ${selectedMirror}`);

        // 3. Susun URL Final (Standard Ninja URL)
        // Format: [URL]/download?use_mirror=[ID]
        const finalDownloadUrl = `${cleanUrl}/download?use_mirror=${selectedMirror}`;

        console.log(`🚀 Ninja Attack! Downloading: ${fileName}`);

        // 4. Jalankan CURL dengan flag -L (Follow Redirect) dan -o (Output Name)
        const curlArgs = [
            '-L',                   // WAJIB: Ikuti redirect ke server mirror
            '-A', USER_AGENT,       // Identitas browser
            '-H', `Referer: ${mirrorPageUrl}`,
            '--retry', '10',        // Gigih: Coba lagi kalau gagal
            '--retry-delay', '3',
            '--connect-timeout', '30',
            '-o', fileName,         // Simpan dengan nama asli
            finalDownloadUrl
        ];

        // Gunakan spawn dengan 'inherit' agar progress bar CURL muncul di log GitHub
        const ninjaDownload = spawn('curl', curlArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        ninjaDownload.on('close', (code) => {
            if (code === 0) {
                console.log(`\n✨ Mission Accomplished: ${fileName}`);
                // Simpan nama file agar bisa dibaca step upload di YAML
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

// Eksekusi berdasarkan environment variable dari GitHub Action
const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadNinjaSF(PAYLOAD_URL);
} else {
    console.error("No URL provided in PAYLOAD_URL");
    process.exit(1);
}
