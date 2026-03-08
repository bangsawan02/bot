const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

chromium.use(StealthPlugin());

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

// Fungsi untuk mengubah cookie format Netscape (curl) ke JSON (Playwright)
function parseNetscapeCookies(filePath) {
    const cookies = [];
    const content = fs.readFileSync(filePath, 'utf8');
    content.split('\n').forEach(line => {
        if (!line.trim() || line.startsWith('#')) return;
        const parts = line.split('\t');
        if (parts.length < 7) return;

        cookies.push({
            name: parts[5],
            value: parts[6].trim(),
            domain: parts[0].startsWith('.') ? parts[0] : parts[0],
            path: parts[2],
            expires: parseInt(parts[4]),
            httpOnly: false,
            secure: parts[3] === 'TRUE'
        });
    });
    return cookies;
}

async function hybridNinjaDownload(targetUrl) {
    const cookieFile = 'cookies.txt';
    const downloadPage = targetUrl.split('?')[0].endsWith('/download') ? targetUrl : `${targetUrl.replace(/\/$/, '')}/download`;

    try {
        console.log(`🍪 Step 1: Harvesting cookies with CURL...`);
        // Kita pancing SourceForge agar memberikan cookie session awal
        execSync(`curl -s -L -c ${cookieFile} -A "${USER_AGENT}" -o /dev/null "https://sourceforge.net/"`);
        
        const cookies = parseNetscapeCookies(cookieFile);
        console.log(`✅ ${cookies.length} cookies harvested.`);

        console.log(`🎭 Step 2: Injecting cookies into Playwright...`);
        const browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ userAgent: USER_AGENT });
        
        // Suntikkan cookie hasil curl tadi
        await context.addCookies(cookies);

        const page = await context.newPage();
        
        console.log(`🌐 Step 3: Navigating to download page: ${downloadPage}`);
        
        // Siapkan listener untuk download
        const downloadPromise = page.waitForEvent('download', { timeout: 90000 });

        // Pergi ke halaman download
        await page.goto(downloadPage, { waitUntil: 'networkidle' });

        console.log("⏳ Waiting for SourceForge to process (Countdown)...");

        // Jika ada tombol manual, klik saja untuk mempercepat
        const manualBtn = page.locator('a.direct-download');
        if (await manualBtn.isVisible()) {
            await manualBtn.click();
        }

        const download = await downloadPromise;
        const fileName = download.suggestedFilename();
        const savePath = path.join(process.cwd(), fileName);

        console.log(`🚀 Final Attack! Downloading: ${fileName}`);
        await download.saveAs(savePath);

        // Validasi
        const stats = fs.statSync(savePath);
        if (stats.size / (1024 * 1024) < 10) throw new Error("File too small. Still hit by HTML gate.");

        console.log(`\n✨ Mission Accomplished: ${fileName} (${(stats.size / (1024 * 1024)).toFixed(2)} MB)`);
        fs.writeFileSync('downloaded_filename.txt', fileName);

        await browser.close();
        if (fs.existsSync(cookieFile)) fs.unlinkSync(cookieFile);
        process.exit(0);

    } catch (error) {
        console.error(`\n💀 Hybrid Ninja Failed: ${error.message}`);
        if (fs.existsSync(cookieFile)) fs.unlinkSync(cookieFile);
        process.exit(1);
    }
}

const url = process.env.PAYLOAD_URL;
if (url) hybridNinjaDownload(url);
