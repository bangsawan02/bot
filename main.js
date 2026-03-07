const axios = require('axios');
const fs = require('fs');
const { spawn } = require('child_process');
const { URL } = require('url');

// Identitas browser standar untuk menghindari 403
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        console.log(`🔎 Analyzing URL: ${targetUrl}`);
        const parsedUrl = new URL(targetUrl);
        const pathParts = parsedUrl.pathname.split('/').filter(p => p !== '');

        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');
        const downloadIndex = pathParts.indexOf('download');

        if (projectIndex === -1 || filesIndex === -1) {
            throw new Error("Bukan URL SourceForge yang valid.");
        }

        const projectName = pathParts[projectIndex + 1];
        const endPathIndex = downloadIndex !== -1 ? downloadIndex : pathParts.length;
        const filePath = pathParts.slice(filesIndex + 1, endPathIndex).join('/');
        const fileName = pathParts[endPathIndex - 1];

        console.log(`📦 Project: ${projectName}`);
        console.log(`📂 File Path: ${filePath}`);

        // URL API Mirror
        const mirrorChoicesUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(mirrorChoicesUrl);
        console.log(`🔗 Fetching mirrors from SourceForge API...`);
        
        // --- 1. BYPASS 403 DI AXIOS ---
        // SourceForge butuh header AJAX yang lengkap agar tidak menolak request JSON
        const response = await axios.get(mirrorChoicesUrl, {
            headers: { 
                'User-Agent': USER_AGENT,
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Referer': targetUrl,
                'X-Requested-With': 'XMLHttpRequest' 
            }
        });

        const mirrors = response.data && response.data.mirrors ? response.data.mirrors : response.data;
        if (!mirrors || !Array.isArray(mirrors) || mirrors.length === 0) {
            throw new Error("Tidak ada mirror yang ditemukan atau format JSON berubah.");
        }

        // Ambil top 5 mirror
        const directUrls = mirrors.slice(0, 5).map(m => 
            `https://${m.shortname}.dl.sourceforge.net/project/${projectName}/${filePath}`
        );

        console.log(`✅ Found ${directUrls.length} mirrors. Starting Aria2c...`);

        // --- 2. BYPASS 403 DI ARIA2C ---
        // Jika Aria2c tidak diberi header ini, server mirror akan merespon 403
        const aria2Args = [
            '-x16', '-s16', '-j16', '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            `--user-agent=${USER_AGENT}`,            // PENTING!
            `--header=Referer: ${targetUrl}`,        // PENTING!
            '-o', fileName,
            ...directUrls
        ];

        const aria2 = spawn('aria2c', aria2Args);

        aria2.stdout.on('data', (data) => {
            const output = data.toString();
            if (output.includes('%')) console.log(output.trim());
        });

        aria2.stderr.on('data', (data) => {
            console.error(`[Aria2c Error]: ${data}`);
        });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`✨ Download Success: ${fileName}`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
            } else {
                console.error("❌ Aria2c exited with error code:", code);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error("❌ Error:", error.message);
        if (error.response) {
            console.error(`Status: ${error.response.status} - Pastikan IP GitHub tidak diblokir total oleh CF.`);
        }
        process.exit(1);
    }
}

// Eksekusi
const url = process.env.PAYLOAD_URL || process.argv[2];
if (url) {
    downloadSourceForge(url);
} else {
    console.error("URL tidak ditemukan. Gunakan argumen atau set PAYLOAD_URL.");
}
