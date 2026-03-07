const axios = require('axios');
const fs = require('fs');
const cheerio = require('cheerio');
const { spawn } = require('child_process');

// User-Agent browser asli
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

async function downloadSourceForge(targetUrl) {
    try {
        // 1. Ubah URL download biasa ke URL mirror_choices
        // Contoh: .../iso/download -> .../settings/mirror_choices?projectname=...&filename=...
        let mirrorPageUrl = targetUrl;
        if (targetUrl.includes('/download')) {
            const cleanUrl = targetUrl.replace('/download', '');
            const parts = new URL(cleanUrl).pathname.split('/').filter(p => p !== '');
            const projectName = parts[1];
            const filePath = parts.slice(3).join('/');
            mirrorPageUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
        }

        console.log(`🔎 Fetching Mirror Page: ${mirrorPageUrl}`);

        // 2. Ambil HTML-nya
        const response = await axios.get(mirrorPageUrl, {
            headers: { 
                'User-Agent': USER_AGENT,
                'Accept': 'text/html',
                'Referer': 'https://sourceforge.net/'
            }
        });

        // 3. Scraping elemen ul#mirrorList > li (Persis logika Python kamu)
        const $ = cheerio.load(response.data);
        const mirrorIds = [];
        
        $('ul#mirrorList > li').each((i, el) => {
            const id = $(el).attr('id');
            if (id) mirrorIds.push(id);
        });

        if (mirrorIds.length === 0) {
            console.log("HTML Response:", response.data.substring(0, 500)); // Debugging
            throw new Error("Gagal menemukan list mirror (li#id). SourceForge mungkin mendeteksi bot.");
        }

        console.log(`✅ Ditemukan ${mirrorIds.length} mirror ID: ${mirrorIds.slice(0, 3).join(', ')}...`);

        // 4. Susun link download menggunakan parameter ?use_mirror=ID
        // Link format: [URL_ASLI_TANPA_DOWNLOAD]?use_mirror=[ID]
        const baseDownloadUrl = targetUrl.endsWith('/download') ? targetUrl.replace('/download', '') : targetUrl;
        const finalUrls = mirrorIds.map(id => `${baseDownloadUrl}?use_mirror=${id}`);

        // Ambil nama file dari URL
        const fileName = baseDownloadUrl.split('/').pop();

        console.log(`🚀 Memulai Aria2c untuk file: ${fileName}`);

        // 5. Jalankan Aria2c dengan semua mirror
        const aria2Args = [
            '-x16', '-s16', '-j16', '-k1M',
            '--file-allocation=none',
            '--check-certificate=false',
            `--user-agent=${USER_AGENT}`,
            `--header=Referer: ${mirrorPageUrl}`,
            '-o', fileName,
            ...finalUrls // Masukkan semua URL mirror
        ];

        const aria2 = spawn('aria2c', aria2Args);

        aria2.stdout.on('data', (data) => {
            const out = data.toString();
            if (out.includes('%')) process.stdout.write(`\r${out.trim()}`);
        });

        aria2.on('close', (code) => {
            if (code === 0) {
                console.log(`\n✨ Download Berhasil: ${fileName}`);
                fs.writeFileSync('downloaded_filename.txt', fileName);
            } else {
                console.error("\n❌ Aria2c Gagal.");
                process.exit(1);
            }
        });

    } catch (error) {
        console.error("❌ Error:", error.message);
        process.exit(1);
    }
}

const url = process.env.PAYLOAD_URL;
downloadSourceForge(url);
