const axios = require('axios');
const fs = require('fs');
const { spawn } = require('child_process');
const { URL } = require('url');

async function downloadSourceForge(targetUrl) {
    try {
        console.log(`🔎 Analyzing URL: ${targetUrl}`);
        const parsedUrl = new URL(targetUrl);
        const pathParts = parsedUrl.pathname.split('/').filter(p => p !== '');

        // Struktur SF: /projects/[PROJECT_NAME]/files/[SUB_DIR]/[FILENAME]/download
        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');
        const downloadIndex = pathParts.indexOf('download');

        if (projectIndex === -1 || filesIndex === -1) {
            throw new Error("Bukan URL SourceForge yang valid.");
        }

        const projectName = pathParts[projectIndex + 1];
        // Mengambil semua bagian di antara 'files' dan 'download' (atau end of path)
        const endPathIndex = downloadIndex !== -1 ? downloadIndex : pathParts.length;
        const filePath = pathParts.slice(filesIndex + 1, endPathIndex).join('/');
        const fileName = pathParts[endPathIndex - 1];

        console.log(`📦 Project: ${projectName}`);
        console.log(`📂 File Path: ${filePath}`);

        // --- STRATEGI: API MIRROR CHOICES (JSON FORMAT) ---
        const mirrorChoicesUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}&format=json`;
        
        console.log(`🔗 Fetching mirrors from: ${mirrorChoicesUrl}`);
        const response = await axios.get(mirrorChoicesUrl, {
            headers: { 'User-Agent': 'Mozilla/5.0' }
        });

        const mirrors = response.data.mirrors;
        if (!mirrors || mirrors.length === 0) {
            throw new Error("Tidak ada mirror yang ditemukan.");
        }

        // Ambil top 5 mirror dan buat direct link-nya
        const directUrls = mirrors.slice(0, 5).map(m => 
            `https://${m.shortname}.dl.sourceforge.net/project/${projectName}/${filePath}`
        );

        console.log(`✅ Found ${directUrls.length} mirrors. Starting Aria2c...`);

        // --- EKSEKUSI ARIA2C ---
        const aria2Args = [
            '-x16', '-s16', '-j16', '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            '-o', fileName,
            ...directUrls // Aria2c akan mencoba semua URL ini secara paralel
        ];

        const aria2 = spawn('aria2c', aria2Args);

        aria2.stdout.on('data', (data) => {
            const output = data.toString();
            // Simpan log ke console agar terlihat di GitHub Actions
            if (output.includes('%')) console.log(output.trim());
        });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`✨ Download Success: ${fileName}`);
                // WAJIB: Tulis nama file untuk step YAML berikutnya
                fs.writeFileSync('downloaded_filename.txt', fileName);
            } else {
                console.error("❌ Aria2c exited with error code:", code);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error("❌ Error:", error.message);
        process.exit(1);
    }
}

// Jalankan berdasarkan PAYLOAD_URL dari environment GitHub Action
const url = process.env.PAYLOAD_URL;
if (url) {
    downloadSourceForge(url);
} else {
    console.error("URL tidak ditemukan di environment variable.");
}
