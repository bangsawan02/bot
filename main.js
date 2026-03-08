const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

// Gunakan Stealth Plugin agar tidak terdeteksi sebagai bot Playwright
chromium.use(StealthPlugin());

async function downloadWithPlaywright(targetUrl) {
    console.log(`🔎 Launching Stealth Browser: ${targetUrl}`);
    
    const browser = await chromium.launch({ 
        headless: true, // Wajib true di GitHub Actions
        args: ['--no-sandbox', '--disable-setuid-sandbox'] 
    });

    const context = await browser.newContext({
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        viewport: { width: 1280, height: 720 }
    });

    const page = await context.newPage();

    try {
        // Pastikan URL mengarah ke /download
        let downloadPageUrl = targetUrl.split('?')[0];
        if (!downloadPageUrl.endsWith('/download')) {
            downloadPageUrl = downloadPageUrl.replace(/\/$/, '') + '/download';
        }

        console.log(`🌐 Navigating to: ${downloadPageUrl}`);

        // Buat promise untuk menangkap event download
        const downloadPromise = page.waitForEvent('download', { timeout: 60000 });

        // Pergi ke halaman download
        await page.goto(downloadPageUrl, { waitUntil: 'networkidle' });

        console.log("⏳ Waiting for SourceForge countdown (5-10s)...");
        
        // Terkadang harus klik manual jika auto-download tidak jalan
        const manualLink = page.locator('a.direct-download, a.button.green');
        if (await manualLink.isVisible()) {
            console.log("clicking manual download button...");
            await manualLink.click();
        }

        // Tunggu sampai SourceForge melempar file aslinya
        const download = await downloadPromise;
        const fileName = download.suggestedFilename();
        const downloadPath = path.join(process.cwd(), fileName);

        console.log(`🚀 Download Started: ${fileName}`);

        // Simpan file ke disk
        await download.saveAs(downloadPath);
        
        // Verifikasi ukuran file
        const stats = fs.statSync(downloadPath);
        const sizeMB = stats.size / (1024 * 1024);

        if (sizeMB < 10) {
            throw new Error(`File terlalu kecil (${sizeMB.toFixed(2)}MB). Kemungkinan hanya HTML.`);
        }

        console.log(`\n✨ Mission Accomplished: ${fileName} (${sizeMB.toFixed(2)} MB)`);
        
        // Simpan nama file untuk digunakan di workflow selanjutnya
        fs.writeFileSync('downloaded_filename.txt', fileName);

        await browser.close();
        process.exit(0);

    } catch (error) {
        console.error(`\n💀 Playwright Error: ${error.message}`);
        await browser.close();
        process.exit(1);
    }
}

const PAYLOAD_URL = process.env.PAYLOAD_URL;
if (PAYLOAD_URL) {
    downloadWithPlaywright(PAYLOAD_URL);
} else {
    console.error("No PAYLOAD_URL provided.");
    process.exit(1);
}
