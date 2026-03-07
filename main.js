const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');
const { URL } = require('url');

// User-Agent yang konsisten agar tidak dideteksi sebagai bot
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        console.log(`🔎 Analyzing URL: ${targetUrl}`);

        // 1. Parsing Path untuk mendapatkan Project Name dan File Path
        const cleanUrl = targetUrl.replace('/download', '');
        const urlObj = new URL(cleanUrl);
        const pathParts = urlObj.pathname.split('/').filter(p => p !== '');

        // Struktur SF biasanya: /projects/[project]/files/[path/to/file]
        const projectIndex = pathParts.indexOf('projects');
        const filesIndex = pathParts.indexOf('files');
        
        if (projectIndex === -1 || filesIndex === -1) {
            throw new Error("Bukan URL SourceForge yang valid (Missing /projects/ or /files/).");
        }

        const projectName = pathParts[projectIndex + 1];
        const filePath = pathParts.slice(filesIndex + 1).join('/');
        const fileName = pathParts[pathParts.length - 1];

        // 2. Susun URL Mirror Choices (Halaman HTML)
        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        console.log(`🔗 Fetching Mirror Page via CURL: ${mirrorPageUrl}`);

        // 3. Ambil HTML menggunakan CURL (Bypass Axios 403)
        const curlCmd = `curl -L -s -A "${USER_AGENT}" \
            -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8" \
            -H "Accept-Language: en-US,en;q=0.5" \
            -H "Referer: https://sourceforge.net/" \
            -H "Upgrade-Insecure-Requests: 1" \
            "${mirrorPageUrl}"`;

        const html = execSync(curlCmd).toString();

        // 4. Scraping ID Mirror dari ul#mirrorList > li
        const $ = cheerio.load(html);
        const mirrorIds = [];
        
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            if (html.includes("Challenge") || html.includes("Cloudflare")) {
                throw new Error("IP GitHub diblokir oleh Cloudflare/Challenge SourceForge.");
            }
            throw new Error("Gagal mengambil list mirror. Struktur HTML mungkin berubah.");
        }

        console.log(`✅ Ditemukan ${mirrorIds.length} mirror: ${mirrorIds.slice(0, 5).join(', ')}...`);

        // 5. Susun Download URLs (Logika identik dengan Python set_url)
        // Format: https://downloads.sourceforge.net/project/[project]/[file]?use_mirror=[id]
        const baseDownloadUrl = `https://downloads.sourceforge.net/project/${projectName}/${filePath}`;
        
        const downloadUrls = mirrorIds.map(id => {
            const dUrl = new URL(baseDownloadUrl);
            dUrl.searchParams.set('use_mirror', id);
            dUrl.searchParams.set('viasf', '1');
            return dUrl.toString();
        });

        // 6. Jalankan Aria2c
        console.log(`🚀 Starting Download: ${fileName} ${downloadUrls}`);
        
        const aria2Args = [
            '--console-log-level=warn',
            '-x16', 
            '-s16', 
            '-j16', 
            '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            `--user-agent=${USER_AGENT}`,
            `--header=Referer: ${mirrorPageUrl}`,
            '-o', fileName,
            ...downloadUrls 
        ];

        // Menggunakan inherit agar log aria2c muncul di GitHub Action secara realtime
        const aria2 = spawn('aria2c', aria2Args, { stdio: ['ignore', 'inherit', 'inherit'] });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`\n✨ Download Success: ${fileName}`);
                // Tulis nama file untuk digunakan step upload.py di YAML
                fs.writeFileSync('downloaded_filename.txt', fileName);
                process.exit(0);
            } else {
                console.error(`\n❌ Aria2c Failed with exit code: ${code}`);
                process.exit(1);
            }
        });

    } catch (error) {
        console.error(`❌ Script Error: ${error.message}`);
        process.exit(1);
    }
}

// Ambil URL dari Environment Variable (sesuai workflow YAML)
const PAYLOAD_URL = process.env.PAYLOAD_URL;

if (PAYLOAD_URL) {
    downloadSourceForge(PAYLOAD_URL);
} else {
    console.error("Error: PAYLOAD_URL environment variable is not set.");
    process.exit(1);
}
