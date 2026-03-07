const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const { spawn } = require('child_process');
const FormData = require('form-data');

class DownloaderBot {
    constructor(url) {
        this.url = url;
        this.botToken = process.env.BOT_TOKEN;
        this.ownerId = process.env.OWNER_ID;
        this.initialMessageId = null;
        this.browser = null;
        this.context = null;
        this.selectors = this._loadSelectorsByDomain();
    }

    _loadSelectorsByDomain() {
        try {
            const data = fs.readJsonSync('selectors.json');
            const domain = new URL(this.url).hostname.replace('www.', '');
            return data[domain] || data['default'];
        } catch (e) {
            return ["a[href^='http']", "a:has-text('Download')", "#downloadButton", "button:has-text('Download')"];
        }
    }

    async _sendTelegramMessage(text) {
        if (!this.botToken || !this.ownerId) return;
        try {
            const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
                chat_id: this.ownerId, text, parse_mode: "Markdown"
            });
            this.initialMessageId = res.data.result.message_id;
        } catch (e) {}
    }

    async _editTelegramMessage(text) {
        if (!this.initialMessageId) return;
        try {
            await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                chat_id: this.ownerId, message_id: this.initialMessageId, text, parse_mode: "Markdown"
            });
        } catch (e) {}
    }

    async _sendScreenshot(page, caption) {
        try {
            const screenshotPath = 'debug.png';
            await page.screenshot({ path: screenshotPath, fullPage: true });
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, { headers: form.getHeaders() });
            fs.removeSync(screenshotPath);
        } catch (e) {}
    }

    // --- ENGINE DOWNLOAD ARIA2C ---
    async _downloadWithAria2(url) {
        await this._editTelegramMessage(`🚀 **Aria2c Active:** Sikat via Direct Link...`);
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', ['-x16', '-s16', '--summary-interval=0', url]);
            aria.on('close', (c) => {
                if (c === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selectors.json', 'package.json'].includes(f) && !f.endsWith('.png'));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error("Aria2 Error"));
            });
        });
    }

    // --- ENGINE DOWNLOAD PLAYWRIGHT ---
    async _handlePlaywrightDownload(download) {
        const filename = download.suggestedFilename();
        const savePath = path.join(process.cwd(), filename);
        await this._editTelegramMessage(`⬇️ **Playwright Active:** Mengunduh \`${filename}\`...`);
        await download.saveAs(savePath);
        return filename;
    }

    // --- MAIN LOGIC (2X PERCOBAAN) ---
    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        // Block ads agar load cepat
        await this.context.route('**/*', (route) => {
            if (['google-analytics', 'popads', 'adskeeper', 'doubleclick'].some(d => route.request().url().includes(d))) return route.abort();
            route.continue();
        });

        await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Menganalisa halaman...`);

            // 1. Ambil Tab Paling Baru (Ini handle otomatis kalo "pindah tab" atau "redirect")
            const allPages = this.context.pages();
            currentPage = allPages[allPages.length - 1]; 
            
            // Tunggu sebentar biar JS di halaman selesai render
            await currentPage.waitForTimeout(3000); 

            // Pasang kuping buat dengerin event download dari SEMUA tab
            const downloadPromise = this.context.waitForEvent('download', { timeout: 15000 }).catch(() => null);
            let selectorClicked = false;

            // 2. Loop mencari selector
            for (const selector of this.selectors) {
                try {
                    const btn = currentPage.locator(selector).first();
                    await btn.waitFor({ state: 'attached', timeout: 5000 });

                    // 3. Jika Ketemu, Cek Href
                    const href = await btn.getAttribute('href');
                    if (href && href.startsWith('http') && !href.includes('javascript:')) {
                        await this._editTelegramMessage(`🔗 Href ditemukan pada \`${selector}\``);
                        return await this._downloadWithAria2(href); // Langsung lempar ke Aria2c dan selesai
                    }

                    // 4. Jika tak ada href, Lanjut Klik
                    await this._editTelegramMessage(`🎯 Force Click pada \`${selector}\``);
                    await btn.scrollIntoViewIfNeeded().catch(() => null);
                    await btn.click({ force: true });
                    
                    selectorClicked = true;
                    break; // Berhenti mencari selector lain karena kita sudah ngeklik satu
                } catch (e) {
                    // Selector tidak ada, lanjut ke selector berikutnya di list
                    continue; 
                }
            }

            // 5. Cek apakah ada progress download setelah klik
            if (selectorClicked) {
                await this._editTelegramMessage(`⏳ Menunggu respon dari klik...`);
                const download = await downloadPromise; // Nunggu maks 15 detik

                if (download) {
                    return await this._handlePlaywrightDownload(download); // Download berjalann!
                }
            } else {
                await this._editTelegramMessage(`⚠️ Tidak ada selector yang cocok di percobaan ${attempt}.`);
            }

            // 6. Jika tidak ada download (redirect/refresh/pindah tab), loop berulang ke attempt 2.
            // Di putaran kedua, dia akan mengambil `currentPage` yang paling baru lagi.
        }

        // Jika sampai di sini, artinya 2x putaran sudah habis dan zonk.
        const finalPage = this.context.pages().pop();
        await this._sendScreenshot(finalPage, `❌ Gagal memicu download setelah 2x percobaan logika.`);
        throw new Error("Gagal mendownload: Tidak ada respon valid dari web.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Memproses Target...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            
            if (finalFile) {
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai:** \`${finalFile}\``);
            }
        } catch (e) {
            await this._editTelegramMessage(`❌ **Error:** ${e.message}`);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
