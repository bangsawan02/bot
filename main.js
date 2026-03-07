const { chromium, devices } = require('playwright-extra');
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
            return ["a[href^='http']", "form", "button:has-text('Download')"];
        }
    }

    _humanSize(bytes) {
        if (!bytes || bytes === 0) return "0 B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

    // --- TELEGRAM HELPERS ---
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
            }).catch(() => {});
        } catch (e) {}
    }

    async _sendScreenshot(caption) {
        if (!this.context || !this.botToken) return;
        try {
            const pages = this.context.pages();
            const page = pages[pages.length - 1]; // Ambil tab terakhir yang aktif
            if (!page) return;

            const screenshotPath = 'error_debug.png';
            await page.screenshot({ path: screenshotPath, fullPage: true });
            
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));
            
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, { headers: form.getHeaders() });
            fs.removeSync(screenshotPath);
        } catch (e) {
            console.log("Gagal kirim screenshot:", e.message);
        }
    }

    // --- ARIA2C ENGINE ---
    async _downloadWithAria2(url) {
        if (!url) return null;
        await this._editTelegramMessage(`🚀 **Aria2c:** Menarik file...`);
        
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', [
                '-x16', '-s16', '--summary-interval=3', '--console-log-level=notice',
                '--file-allocation=none', '--auto-file-renaming=false', url
            ]);

            let lastUpdate = 0;
            let fileName = "Mengidentifikasi...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
                if (nameMatch && (fileName === "Mengidentifikasi..." || !fileName)) {
                    fileName = nameMatch[1].trim();
                }

                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const percent = progressMatch[1];
                    const speed = progressMatch[2];
                    const now = Date.now();
                    if (now - lastUpdate > 4000) {
                        lastUpdate = now;
                        await this._editTelegramMessage(`⬇️ **Aria2c Progress**\n\n📄 File: \`${fileName}\`\n📊 Progress: \`${percent}%\`\n⚡ Speed: \`${speed}\``);
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => 
                        !['main.js', 'selectors.json', 'package.json'].includes(f) && 
                        !f.endsWith('.png') && !f.endsWith('.aria2')
                    );
                    if (files.length === 0) return reject(new Error("File tidak ditemukan!"));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error(`Aria2 error code: ${code}`));
            });
        });
    }

    // --- LOGIKA UTAMA ---
    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        await this.context.route('**/*', (route) => {
            if (['analytics', 'adskeeper', 'popads', 'doubleclick'].some(d => route.request().url().includes(d))) return route.abort();
            route.continue();
        });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Memindai (Mobile Mode)...`);
            const downloadPromise = this.context.waitForEvent('download', { timeout: 45000 }).catch(() => null);

            try {
                await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
                await currentPage.waitForTimeout(3000);

                const pages = this.context.pages();
                currentPage = pages[pages.length - 1]; 

                let actionDone = false;
                for (const selector of this.selectors) {
                    try {
                        const el = currentPage.locator(selector).first();
                        await el.waitFor({ state: 'attached', timeout: 5000 });

                        const tag = await el.evaluate(e => e.tagName.toLowerCase());
                        if (tag === 'form') {
                            await this._editTelegramMessage(`📝 Submit Form: \`${selector}\``);
                            await el.evaluate(f => f.submit());
                            actionDone = true;
                        } else {
                            const href = await el.getAttribute('href');
                            if (href && href.startsWith('http') && !href.includes('javascript:')) {
                                return await this._downloadWithAria2(href);
                            }
                            await this._editTelegramMessage(`🎯 Klik Tombol: \`${selector}\``);
                            await el.click({ force: true });
                            actionDone = true;
                        }
                        if (actionDone) break;
                    } catch (e) { continue; }
                }

                if (actionDone) {
                    const dlObj = await downloadPromise;
                    if (dlObj) {
                        const directUrl = dlObj.url();
                        await dlObj.cancel();
                        return await this._downloadWithAria2(directUrl);
                    }
                    await currentPage.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => null);
                }
            } catch (e) { console.log(`Attempt ${attempt} gagal.`); }
        }
        throw new Error("Gagal: Link download tidak ditemukan sampai akhir.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Start (Mobile Mode)...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ ...devices['iPhone 13'], acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            
            if (finalFile) {
                const size = fs.statSync(finalFile).size;
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n📄 File: \`${finalFile}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
            }
        } catch (e) {
            // KIRIM SCREENSHOT JIKA ERROR
            await this._editTelegramMessage(`❌ **Error:** ${e.message}`);
            await this._sendScreenshot(`Debug Error: ${e.message}`);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
