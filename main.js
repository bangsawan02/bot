const axios = require('axios');
const fs = require('fs');
const { spawn } = require('child_process');
const { URL } = require('url');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        console.log(`🔎 Analyzing URL: ${targetUrl}`);
        const parsedUrl = new URL(targetUrl);
        const pathParts = parsedUrl.pathname.split('/').filter(p => p !== '');

        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');
        
        // Ambil project name
        const projectName = pathParts[projectIndex + 1];
        
        // Ambil path file: cari bagian setelah 'files' sampai sebelum 'download'
        const downloadIndex = pathParts.indexOf('download');
        const endPathIndex = downloadIndex !== -1 ? downloadIndex : pathParts.length;
        const filePath = pathParts.slice(filesIndex + 1, endPathIndex).join('/');
        const fileName = pathParts[endPathIndex - 1];

        console.log(`📦 Project: ${projectName}`);
        console.log(`📂 File Path: ${filePath}`);

        // --- STRATEGI BARU: AUTO-GENERATED DIRECT LINKS ---
        // Karena API sering 403 atau return HTML, kita pakai mirror list yang paling stabil secara manual.
        const knownMirrors = ['versaweb', 'managedway', 'altushost', 'constant', 'fastly'];
        const directUrls = knownMirrors.map(m => 
            `https://${m}.dl.sourceforge.net/project/${projectName}/${filePath}`
        );

        // Tambahkan URL "master" sebagai cadangan terakhir
        directUrls.push(`https://downloads.sourceforge.net/project/${projectName}/${filePath}`);

        console.log(`🚀 Generated ${directUrls.length} potential direct links.`);
        console.log(`📡 Testing first mirror: ${directUrls[0]}`);

        // --- EKSEKUSI ARIA2C ---
        const aria2Args = [
            '-x16', 
            '-s16', 
            '-j16', 
            '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            '--retry-wait=5',
            '--max-tries=10',
            `--user-agent=${USER_AGENT}`,
            `--header=Referer: https://sourceforge.net/projects/${projectName}/files/`,
            '-o', fileName,
            ...directUrls // Aria2c akan otomatis mencoba satu per satu kalau ada yang 403
        ];

        const aria2 = spawn('aria2c', aria2Args);

        aria2.stdout.on('data', (data) => {
            const output = data.toString();
            // Tampilkan progres download agar GitHub Action tidak dianggap stuck
            if (output.includes('%') || output.includes('CN:')) {
                process.stdout.write(`\r${output.trim()}`);
            }
        });

        aria2.stderr.on('data', (data) => {
            console.error(`\n[Aria2c Alert]: ${data}`);
        });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`\n\n✨ Download Success: ${fileName}`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
            } else {
                console.error("\n❌ Aria2c failed. Code:", code);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error("\n❌ Error:", error.message);
        process.exit(1);
    }
}

const url = process.env.PAYLOAD_URL;
downloadSourceForge(url);
