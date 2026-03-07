const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');
const { URL } = require('url');

// User-Agent konsisten
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        console.log(`🔎 Analyzing URL: ${targetUrl}`);

        const cleanUrl = targetUrl.replace('/download', '');
        const urlObj = new URL(cleanUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');

        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');
        
        if (projectIndex === -1 || filesIndex === -1) {
            throw new Error("Bukan URL SourceForge yang valid.");
        }

        const projectName = pathParts[projectIndex + 1];
        const filePath = pathParts.slice(filesIndex + 1).join('/');
        const fileName = pathParts[pathParts.length - 1];

        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(`🔗 Fetching Mirror Page: ${mirrorPageUrl}`);

        // 1. Ambil HTML Mirror List
        const fetchHtmlCmd = `curl -L -s -A "${USER_AGENT}" \
            -H "Referer: https://sourceforge.net/" \
            "${mirrorPageUrl}"`;

        const html = execSync(fetchHtmlCmd).toString();
        const $ = cheerio.load(html);
        const mirrorIds = [];
        
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            throw new Error("Gagal mengambil list mirror. Cek apakah IP diblokir.");
        }

        // 2. Pilih mirror pertama (paling stabil biasanya)
        const selectedMirror = mirrorIds[0];
        const finalDownloadUrl = `https://downloads.sourceforge.net/project/${projectName}/${filePath}?use_mirror=${selectedMirror}&viasf=1`;

        console.log(`✅ Mirror selected: ${selectedMirror}`);
        console.log(`🚀 Starting Download with CURL: ${fileName}`);

        // 3. Jalankan CURL untuk mendownload file
        // -L: follow redirect
        // -#: progress bar simpel
        // -C -: resume download jika terputus
        const curlArgs = [
            '-L', 
            '-A', USER_AGENT,
            '-H', `Referer: ${mirrorPageUrl}`,
            '-o', fileName,
            '--retry', '5',
            '--retry-delay', '2',
            finalDownloadUrl
        ];

        // Pakai spawn agar progress bar curl muncul di console GitHub Actions
        const curlDownload = spawn('curl', curlArgs, { stdio: ['ignore', 'inherit', 'inherit'] });

        curlDownload.on('close', (code) => {
            if (code === 0) {
                console.log(`\n✨ Download Success: ${fileName}`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
                process.exit(0);
            } else {
                console.error(`\n❌ CURL Failed with exit code: ${code}`);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error(`❌ Script Error: ${error.message}`);
        process.exit(1);
    }
}

const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadSourceForge(PAYLOAD_URL);
} else {
    console.error("Error: PAYLOAD_URL not set.");
    process.exit(1);
}
