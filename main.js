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
            return ["form", "a:has-text('Download')", "button:has-text('Download')"];
        }
    }

    // --- TELEGRAM HELPERS (SAMA SEPERTI SEBELUMNYA) ---
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

    // --- DOWNLOAD ENGINES ---
    async _downloadWithAria2(url) {
        await this._editTelegramMessage(`🚀 **Aria2c:** Menarik file dari link langsung...`);
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', ['-x16', '-s16', '--summary-interval=0', url]);
            aria.on('close', (c) => {
                if (c === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selectors.json', 'package.json'].includes(f) && !f.endsWith('.png'));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error("Aria2 gagal"));
            });
        });
    }

    // --- CORE LOGIC ---
    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        // Anti-Ads & Trackers
        await this.context.route('**/*', (route) => {
            const u = route.request().url();
            if (['google-analytics', 'adskeeper', 'popads', 'doubleclick'].some(d => u.includes(d))) return route.abort();
            route.continue();
        });

        await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Memindai halaman...`);
            
            // Ambil tab paling baru jika terjadi pop-up
            const pages = this.context.pages();
            currentPage = pages[pages.length - 1];
            await currentPage.waitForTimeout(4000); 

            const downloadPromise = this.context.waitForEvent('download', { timeout: 20000 }).catch(() => null);
            let actionDone = false;

            for (const selector of this.selectors) {
                try {
                    const element = currentPage.locator(selector).first();
                    await element.waitFor({ state: 'attached', timeout: 5000 });

                    // 1. CEK TAG NAME (FORM ATAU BUKAN)
                    const tagName = await element.evaluate(el => el.tagName.toLowerCase());

                    if (tagName === 'form') {
                        await this._editTelegramMessage(`📝 Menemukan FORM: Melakukan Submit...`);
                        await element.evaluate(form => form.submit());
                        actionDone = true;
                    } else {
                        // 2. CEK HREF JIKA BUKAN FORM
                        const href = await element.getAttribute('href');
                        if (href && href.startsWith('http') && !href.includes('javascript:')) {
                            return await this._downloadWithAria2(href);
                        }

                        await this._editTelegramMessage(`🎯 Menekan Tombol: \`${selector}\``);
                        await element.click({ force: true });
                        actionDone = true;
                    }
                    
                    if (actionDone) break; 
                } catch (e) { continue; }
            }

            if (actionDone) {
                await this._editTelegramMessage(`⏳ Menunggu respon (Download atau Redirect)...`);
                const download = await downloadPromise;
                if (download) {
                    const filename = download.suggestedFilename();
                    await download.saveAs(filename);
                    return filename;
                }
                
                // JIKA TIDAK ADA DOWNLOAD:
                // Playwright akan lanjut ke loop attempt berikutnya.
                // Jika responnya adalah "banyak HTML", maka di attempt 2 
                // `currentPage` akan berisi HTML baru tersebut dan memindai selector lagi.
                await currentPage.waitForLoadState('networkidle').catch(() => null);
            }
        }

        await currentPage.screenshot({ path: 'fail.png', fullPage: true });
        throw new Error("Gagal: Timeout atau tidak ada selector yang memicu download.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Memulai...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
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
