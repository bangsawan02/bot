const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const { spawn, execSync } = require('child_process');
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

    // --- PYTHON HANDLER ---
    async _installPythonRequirements() {
        if (fs.existsSync('requirements.txt')) {
            await this._editTelegramMessage("📦 **Python:** Menginstal `requirements.txt`...");
            try {
                execSync('pip install -r requirements.txt', { stdio: 'inherit' });
                console.log("Requirements installed successfully.");
            } catch (e) {
                console.error("Gagal install requirements:", e.message);
            }
        }
    }

    async _runPythonSourceForge() {
        // 1. Install dulu jika ada requirements.txt
        await this._installPythonRequirements();
        
        await this._editTelegramMessage(`🐍 **SourceForge Detected**\nMenjalankan \`main.py\` untuk bypass Cloudflare...`);
        
        return new Promise((resolve, reject) => {
            const py = spawn('python3', ['main.py', this.url], {
                env: { ...process.env, PYTHONUNBUFFERED: '1' }
            });

            py.stdout.on('data', (data) => {
                const out = data.toString();
                console.log(`[Python]: ${out}`);
                // Jika output mengandung informasi progress, edit pesan telegram
                if (out.includes('%') || out.includes('Download')) {
                    this._editTelegramMessage(`🐍 **Python Output:**\n\`${out.trim()}\``);
                }
            });

            py.stderr.on('data', (data) => console.error(`[Python Error]: ${data}`));

            py.on('close', (code) => {
                if (code === 0) resolve(true);
                else reject(new Error(`Python exit code: ${code}`));
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
                        !['main.js', 'main.py', 'selectors.json', 'package.json', 'requirements.txt'].includes(f) && 
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

                let actionDone = false;
                for (const selector of this.selectors) {
                    try {
                        const el = currentPage.locator(selector).first();
                        await el.waitFor({ state: 'attached', timeout: 5000 });
                        
                        const tag = await el.evaluate(e => e.tagName.toLowerCase());
                        if (tag === 'form') {
                            await el.evaluate(f => f.submit());
                            actionDone = true;
                        } else {
                            const href = await el.getAttribute('href');
                            if (href && href.startsWith('http') && !href.includes('javascript:')) {
                                return await this._downloadWithAria2(href);
                            }
                            await el.click({ force: true });
                            actionDone = true;
                        }
                        if (actionDone) break;
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
        if (this.url.includes('sourceforge.net')) {
            try {
                await this._runPythonSourceForge();
                return; 
            } catch (e) {
                await this._editTelegramMessage(`❌ Python Engine Gagal: ${e.message}. Mencoba Playwright...`);
            }
        }

        await this._sendTelegramMessage(`⏳ **Bot Start (Playwright Mode)...**`);
        try {
            this.browser = await chromium.launch({ headless: false, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ ...devices['iPhone 13'], acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            
            if (finalFile) {
                const size = fs.statSync(finalFile).size;
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n📄 File: \`${finalFile}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
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
