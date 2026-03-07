const { execSync, spawn } = require('child_process');
const fs = require('fs');
const cheerio = require('cheerio');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        // 1. Parsing URL untuk mendapatkan Mirror Page
        const cleanUrl = targetUrl.replace('/download', '');
        const urlObj = new URL(cleanUrl);
        const parts = urlObj.pathname.split('/').filter(p => p !== '');
        const projectName = parts[1];
        const filePath = parts.slice(3).join('/');
        const mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;

        console.log(`🔎 Fetching Mirror Page via CURL: ${mirrorPageUrl}`);

        // 2. Gunakan CURL sistem untuk bypass 403 (lebih sakti dari Axios)
        // Kita tambahkan header LENGKAP agar dikira browser asli
        const curlCmd = `curl -L -s -A "${USER_AGENT}" \
            -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8" \
            -H "Accept-Language: en-US,en;q=0.5" \
            -H "Referer: https://sourceforge.net/" \
            "${mirrorPageUrl}"`;

        const html = execSync(curlCmd).toString();

        // 3. Scraping dengan Cheerio
        const $ = cheerio.load(html);
        const mirrorIds = [];
        
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            // Jika masih gagal, cek apakah kita kena captcha/challenge
            if (html.includes("Challenge") || html.includes("Cloudflare")) {
                throw new Error("Terdeteksi Cloudflare/Captcha. IP GitHub diblokir.");
            }
            throw new Error("Gagal menemukan list mirror. HTML tidak sesuai.");
        }

        console.log(`✅ Ditemukan ${mirrorIds.length} mirror.`);

        // 4. Susun Link
        const fileName = filePath.split('/').pop();
        const finalUrls = mirrorIds.slice(0, 10).map(id => `${cleanUrl}?use_mirror=${id}`);

        // 5. Jalankan Aria2c
// 5. Jalankan Aria2c dengan penanganan header yang lebih ketat
        console.log(`🚀 Downloading: ${fileName}`);
        
        const aria2Args = [
            '--console-log-level=warn',
            '-x16', 
            '-s16', 
            '-j16', 
            '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            '--retry-wait=5',
            '--max-tries=10',
            `--user-agent=${USER_AGENT}`,
            `--header=Referer: https://sourceforge.net/`,
            `--header=Accept: */*`,
            '-o', fileName,
            // Bungkus setiap URL dengan tanda kutip untuk keamanan
            ...finalUrls 
        ];

        // Gunakan stdio: 'inherit' agar kita bisa lihat error asli dari aria2c di log GitHub
        const aria2 = spawn('aria2c', aria2Args, { stdio: ['ignore', 'inherit', 'inherit'] });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`\n✨ Selesai: ${fileName}`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
            } else {
                console.error(`\n❌ Aria2c gagal dengan exit code: ${code}`);
                // Cek apakah file parsial ada, jika ya mungkin masalah disk atau koneksi putus
                process.exit(1);
            }
        });

    } catch (error) {
        console.error(`❌ Error: ${error.message}`);
        process.exit(1);
    }
}

const url = process.env.PAYLOAD_URL;
downloadSourceForge(url);
