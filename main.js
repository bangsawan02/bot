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
            const urlObj = new URL(this.url);
            const domain = urlObj.hostname.replace('www.', '');
            return data[domain] || data['default'];
        } catch (e) {
            return ["a:has-text('Download')", "#downloadButton", "#downloadbtn"];
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

    async _downloadWithAria2(url) {
        await this._editTelegramMessage(`🚀 **Aria2c:** Sikat link langsung...`);
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', ['-x16', '-s16', '--summary-interval=0', url]);
            aria.on('close', (c) => {
                if (c === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selectors.json', 'package.json'].includes(f) && !f.endsWith('.png') && !f.endsWith('.txt'));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error("Aria2 gagal"));
            });
        });
    }

    async _processDefault() {
        const page = await this.context.newPage();
        
        // Block ads/popups agar tidak mengganggu klik
        await page.route('**/*', (route) => {
            const url = route.request().url();
            if (['google-analytics', 'doubleclick', 'adskeeper', 'popads', 'onclick'].some(d => url.includes(d))) return route.abort();
            route.continue();
        });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Navigasi (Percobaan ${attempt}/2)...`);
            try {
                await page.goto(this.url, { waitUntil: 'load', timeout: 60000 });
                
                // Pantau download event
                const downloadPromise = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);

                for (const selector of this.selectors) {
                    try {
                        await this._editTelegramMessage(`⏳ Menunggu tombol: \`${selector}\`...`);
                        
                        // TUNGGU SAMPAI TOMBOL MUNCUL (PENTING!)
                        const btn = page.locator(selector).first();
                        await btn.waitFor({ state: 'attached', timeout: 15000 }); 

                        // Cek HREF jika direct link
                        const href = await btn.getAttribute('href');
                        if (href && href.startsWith('http') && !href.includes('javascript:')) {
                            return await this._downloadWithAria2(href);
                        }

                        // Jika bukan link, Klik paksa
                        await btn.scrollIntoViewIfNeeded().catch(() => null);
                        await btn.click({ force: true });
                        
                        // Jeda setelah klik untuk refresh/generate link baru
                        await page.waitForTimeout(5000); 
                    } catch (e) {
                        console.log(`[Log] Selector ${selector} tidak muncul dalam 15 detik.`);
                    }
                }

                const download = await downloadPromise;
                if (download) {
                    const filename = download.suggestedFilename();
                    await download.saveAs(filename);
                    return filename;
                }
            } catch (e) {
                console.log(`Error di attempt ${attempt}: ${e.message}`);
                if (attempt === 2) await this._sendScreenshot(page, "Gagal total setelah 2 percobaan.");
            }
        }
        throw new Error("Proses Berhenti: File tidak ditemukan.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Memulai...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            const finalFile = await this._processDefault();
            if (finalFile) {
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Berhasil:** \`${finalFile}\``);
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
