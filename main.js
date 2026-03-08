const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const { spawn } = require('child_process');

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
            if (fs.existsSync('selectors.json')) {
                const data = fs.readJsonSync('selectors.json');
                const domain = new URL(this.url).hostname.replace('www.', '');
                return data[domain] || data['default'];
            }
        } catch (e) {
            console.log("Gagal load selectors.json, menggunakan default.");
        }
        return ["a[href^='http']", "form", "button:has-text('Download')"];
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
        } catch (e) { console.error("Telegram Error:", e.message); }
    }

    async _editTelegramMessage(text) {
        if (!this.initialMessageId) return await this._sendTelegramMessage(text);
        try {
            await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                chat_id: this.ownerId, message_id: this.initialMessageId, text, parse_mode: "Markdown"
            }).catch(() => {});
        } catch (e) {}
    }

    // --- CURL ENGINE (KHUSUS SOURCEFORGE) ---
    async _downloadWithCurl(url) {
        // Ekstrak nama file dari URL (menghapus /download jika ada)
        const cleanPath = url.split('?')[0].replace(/\/download$/, '');
        const fileName = path.basename(cleanPath) || 'downloaded_file.iso';

        await this._editTelegramMessage(`🎯 **SourceForge Detected**\nMengunduh langsung via \`curl\`...`);

        return new Promise((resolve, reject) => {
            // Gunakan -L untuk follow redirect sesuai dokumentasi SF
            const curl = spawn('curl', ['-L', '-A', 'Mozilla/5.0', '-o', fileName, url], {
                stdio: 'inherit' // Munculkan progress bar di log terminal
            });

            curl.on('close', (code) => {
                if (code === 0) resolve(fileName);
                else reject(new Error(`Curl exit code: ${code}`));
            });
        });
    }

    // --- ARIA2C ENGINE ---
    async _downloadWithAria2(url) {
        if (!url) return null;
        await this._editTelegramMessage(`🚀 **Aria2c:** Menarik file...`);
        
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', [
                '-x16', '-s16', '--summary-interval=3', '--file-allocation=none', '--auto-file-renaming=false', url
            ]);

            let lastUpdate = 0;
            let fileName = "Mengidentifikasi...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
                if (nameMatch && fileName === "Mengidentifikasi...") {
                    fileName = nameMatch[1].trim();
                }

                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const now = Date.now();
                    if (now - lastUpdate > 4000) {
                        lastUpdate = now;
                        await this._editTelegramMessage(`⬇️ **Aria2c Progress**\n\n📄 File: \`${fileName}\`\n📊 Progress: \`${progressMatch[1]}%\`\n⚡ Speed: \`${progressMatch[2]}\``);
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => 
                        !['main.js', 'selectors.json', 'package.json'].includes(f) && 
                        !f.endsWith('.png') && !f.endsWith('.aria2')
                    );
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted.length > 0 ? sorted[0].n : null);
                } else reject(new Error(`Aria2 error code: ${code}`));
            });
        });
    }

    // --- PLAYWRIGHT ENGINE ---
    async _processDefault() {
        let currentPage = await this.context.newPage();
        await this.context.route('**/*', (route) => {
            if (['analytics', 'adskeeper', 'popads', 'doubleclick'].some(d => route.request().url().includes(d))) return route.abort();
            route.continue();
        });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Memindai (Playwright)...`);
            const downloadPromise = this.context.waitForEvent('download', { timeout: 45000 }).catch(() => null);

            try {
                await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
                await currentPage.waitForTimeout(3000);

                for (const selector of this.selectors) {
                    try {
                        const el = currentPage.locator(selector).first();
                        await el.waitFor({ state: 'attached', timeout: 5000 });
                        
                        const tag = await el.evaluate(e => e.tagName.toLowerCase());
                        if (tag === 'form') {
                            await el.evaluate(f => f.submit());
                        } else {
                            const href = await el.getAttribute('href');
                            if (href && href.startsWith('http') && !href.includes('javascript:')) {
                                return await this._downloadWithAria2(href);
                            }
                            await el.click({ force: true });
                        }
                        break; 
                    } catch (e) { continue; }
                }

                const dlObj = await downloadPromise;
                if (dlObj) {
                    const directUrl = dlObj.url();
                    await dlObj.cancel();
                    return await this._downloadWithAria2(directUrl);
                }
            } catch (e) { console.log(`Attempt ${attempt} gagal.`); }
        }
        throw new Error("Gagal: Link download tidak ditemukan.");
    }

    async run() {
        // Jika SourceForge, langsung tembak pakai CURL
        if (this.url.includes('sourceforge.net')) {
            try {
                const finalFile = await this._downloadWithCurl(this.url);
                return this._finish(finalFile);
            } catch (e) {
                await this._editTelegramMessage(`❌ Curl Gagal: ${e.message}. Mencoba Playwright...`);
            }
        }

        await this._sendTelegramMessage(`⏳ **Bot Start (Playwright Mode)...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ ...devices['Desktop Chrome'], acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            if (finalFile) this._finish(finalFile);
            
        } catch (e) {
            await this._editTelegramMessage(`❌ **Error:** ${e.message}`);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }

    _finish(finalFile) {
        if (!finalFile) return;
        const size = fs.statSync(finalFile).size;
        fs.writeFileSync('downloaded_filename.txt', finalFile);
        this._editTelegramMessage(`✅ **Selesai!**\n📄 File: \`${finalFile}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
