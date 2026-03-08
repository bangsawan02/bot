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
        } catch (e) {}
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

    // --- SOURCEFORGE RESOLVER ---
    async _resolveSourceForge(url) {
        try {
            const res = await axios({ url: url, method: "GET", maxRedirects: 0, validateStatus: null });
            if (res.status >= 300 && res.status < 400 && res.headers.location) {
                let redirect = res.headers.location;
                return redirect.startsWith("//") ? "https:" + redirect : redirect;
            }
            const res2 = await axios.get(url, { maxRedirects: 10 });
            return res2.request.res.responseUrl;
        } catch (e) {
            throw new Error("Resolve SourceForge gagal: " + e.message);
        }
    }

    // --- ARIA2C ENGINE ---
    async _downloadWithAria2(url) {
        if (!url) return null;
        
        // Ambil nama file sementara dari URL agar tidak muncul "Detecting..." di awal
        let fileName = url.split('/').pop().split('?')[0] || "File_Detected";
        await this._editTelegramMessage(`🚀 **Aria2c:** Menyiapkan unduhan...\n📄 Target: \`${fileName}\``);

        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', [
                '-x16', '-s16', 
                '--summary-interval=3', 
                '--file-allocation=none', 
                '--auto-file-renaming=false', 
                '--allow-overwrite=true',
                url
            ]);

            let lastUpdate = 0;

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                
                // Regex baru untuk menangkap nama file lebih akurat dari output Aria2
                const nameMatch = output.match(/Saving to: (?:.*\/)?([^\s/]+)/);
                if (nameMatch && nameMatch[1]) {
                    const cleanName = nameMatch[1].trim();
                    if (!cleanName.includes('...') && cleanName !== fileName) {
                        fileName = cleanName;
                    }
                }

                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const now = Date.now();
                    if (now - lastUpdate > 4000) {
                        lastUpdate = now;
                        await this._editTelegramMessage(
                            `⬇️ **Download Progress**\n\n` +
                            `📄 File: \`${fileName}\`\n` +
                            `📊 Progress: \`${progressMatch[1]}%\`\n` +
                            `⚡ Speed: \`${progressMatch[2]}\``
                        );
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.')
                        .filter(f => !f.endsWith('.aria2') && !['main.js', 'package.json', 'selectors.json'].includes(f))
                        .map(f => ({ name: f, time: fs.statSync(f).mtime }))
                        .sort((a, b) => b.time - a.time);
                    resolve(files.length ? files[0].name : null);
                } else {
                    reject(new Error(`Aria2 error code: ${code}`));
                }
            });
        });
    }

    // --- SOURCEFORGE HANDLER ---
    async _handleSourceForge() {
        await this._editTelegramMessage("🔎 **SourceForge:** Mencari mirror terbaik...");
        const resolved = await this._resolveSourceForge(this.url);
        return await this._downloadWithAria2(resolved);
    }

    // --- PLAYWRIGHT HANDLER (DEFAULT) ---
    async _processDefault() {
        const page = await this.context.newPage();
        const downloadPromise = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);

        await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
        await page.waitForTimeout(3000);

        for (const selector of this.selectors) {
            try {
                const el = page.locator(selector).first();
                await el.waitFor({ state: 'attached', timeout: 5000 });
                const href = await el.getAttribute('href');

                if (href && href.startsWith("http") && !href.includes('javascript:')) {
                    return await this._downloadWithAria2(href);
                }

                await el.click({ force: true });
                const dl = await downloadPromise;
                if (dl) {
                    const directUrl = dl.url();
                    await dl.cancel();
                    return await this._downloadWithAria2(directUrl);
                }
            } catch (e) {}
        }
        throw new Error("Gagal menemukan link download otomatis.");
    }

    async run() {
        await this._sendTelegramMessage("⏳ **Bot Started...**");

        try {
            let finalFile;

            if (this.url.includes("sourceforge.net")) {
                finalFile = await this._handleSourceForge();
            } else {
                this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
                this.context = await this.browser.newContext({ ...devices['Desktop Chrome'], acceptDownloads: true });
                finalFile = await this._processDefault();
            }

            if (finalFile) {
                const size = fs.statSync(finalFile).size;
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(
                    `✅ **Selesai!**\n\n` +
                    `📄 File: \`${finalFile}\`\n` +
                    `⚖️ Size: \`${this._humanSize(size)}\``
                );
            }
        } catch (e) {
            await this._editTelegramMessage(`❌ **Error:** \`${e.message}\``);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
