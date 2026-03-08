const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

async function runCurlNinja(targetUrl) {
    // 1. Ekstrak nama file dari URL secara otomatis
    const urlParts = targetUrl.split('/');
    // Mengambil bagian sebelum '/download'
    const fileName = urlParts[urlParts.length - 2] || 'downloaded_file.iso';

    console.log(`🥷 Ninja Mission Start!`);
    console.log(`📄 Filename: ${fileName}`);
    console.log(`🔗 URL: ${targetUrl}\n`);

    // 2. Susun argumen curl sesuai permintaanmu
    // -L : Follow redirect (Wajib untuk SourceForge)
    // -o : Output file name
    const curlArgs = [
        '-L', 
        '-o', fileName, 
        targetUrl
    ];

    // 3. Jalankan curl sebagai child process
    // 'inherit' membuat progress bar curl muncul di console
    const curlProcess = spawn('curl', curlArgs, { stdio: 'inherit' });

    curlProcess.on('close', (code) => {
        if (code === 0) {
            console.log(`\n✨ Mission Accomplished: ${fileName} saved successfully.`);
            
            // Simpan nama file ke txt untuk step GitHub Actions berikutnya (jika perlu)
            fs.writeFileSync('downloaded_filename.txt', fileName);
            process.exit(0);
        } else {
            console.error(`\n❌ Ninja Failed. Curl exited with code: ${code}`);
            process.exit(1);
        }
    });
}

// Ambil URL dari Environment Variable PAYLOAD_URL (standar GitHub Actions)
const PAYLOAD_URL = process.env.PAYLOAD_URL || "https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS14/OpenGApps/Generic/Bliss-v14.10.3-x86_64-OFFICIAL-opengapps-20241012.iso/download";

runCurlNinja(PAYLOAD_URL);
