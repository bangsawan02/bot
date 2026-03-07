const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const { spawn } = require('child_process');
const fs = require('fs');

chromium.use(StealthPlugin());

async function getDirectLink(targetUrl) {
    console.log(`🔎 Launching Stealth Browser for: ${targetUrl}`);
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    });
    const page = await context.newPage();

    let finalDownloadUrl = null;

    // Intersep request untuk menangkap URL yang mengandung mirror/download
    page.on('download', download => {
        finalDownloadUrl = download.url();
        console.log(`✅ Intercepted Download URL: ${finalDownloadUrl}`);
    });

    try {
        await page.goto(targetUrl, { waitUntil: 'networkidle', timeout: 60000 });
        
        // Tunggu sebentar karena SF biasanya hitung mundur 5 detik
        console.log("⏳ Waiting for SourceForge countdown and redirect...");
        
        // Kita paksa tunggu sampai ada navigasi atau download trigger
        await page.waitForTimeout(10000); 

        // Jika belum dapat, coba cari tombol "Problems Downloading?" lalu klik mirror pertama
        if (!finalDownloadUrl) {
            console.log("🤔 Auto-download didn't start. Trying to fetch from mirror list...");
            const mirrorUrl = targetUrl.replace('/download', '') + '/settings/mirror_choices';
            await page.goto(mirrorUrl, { waitUntil: 'networkidle' });
            
            // Klik mirror pertama yang tersedia
            const firstMirror = await page.$('ul#mirrorList > li > a');
            if (firstMirror) {
                const href = await firstMirror.getAttribute('href');
                finalDownloadUrl = href.startsWith('http') ? href : `https://sourceforge.net${href}`;
            }
        }

        await browser.close();
        return finalDownloadUrl;

    } catch (err) {
        console.error("❌ Browser Error:", err.message);
        await browser.close();
        return null;
    }
}

async function startAria(url) {
    if (!url) {
        console.error("❌ No valid download URL found.");
        process.exit(1);
    }

    const fileName = url.split('/').pop().split('?')[0];
    console.log(`🚀 Starting Aria2c for: ${fileName}`);

    const aria2Args = [
        '--console-log-level=warn',
        '-x16', '-s16', '-k1M',
        '--file-allocation=none',
        '--check-certificate=false',
        `--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36`,
        '-o', fileName,
        url
    ];

    const aria2 = spawn('aria2c', aria2Args, { stdio: 'inherit' });

    aria2.on('close', (code) => {
        if (code === 0) {
            console.log(`✨ Success: ${fileName}`);
            fs.writeFileSync('downloaded_filename.txt', fileName);
            process.exit(0);
        } else {
            console.error(`❌ Aria2c error code: ${code}`);
            process.exit(1);
        }
    });
}

(async () => {
    const url = process.env.PAYLOAD_URL;
    const directLink = await getDirectLink(url);
    await startAria(directLink);
})();
