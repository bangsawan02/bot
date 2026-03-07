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
            return ["a[href^='http']", "form", "button:has-text('Download')", "a:has-text('Start Download')"];
        }
    }

    _humanSize(bytes) {
        if (!bytes || bytes === 0) return "0 B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

    // --- TELEGRAM LOGIC ---
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
        if (!this.context) return;
        try {
            const pages = this.context.pages();
            const page = pages[pages.length - 1] || pages[0];
            const screenshotPath = 'debug.png';
            await page.screenshot({ path: screenshotPath });
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, { headers: form.getHeaders() });
            fs.removeSync(screenshotPath);
        } catch (e) {}
    }

    // --- ARIA2C ENGINE ---
    async _downloadWithAria2(url) {
        await this._editTelegramMessage(`🚀 **Aria2c Engine:** Sikat!`);
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', [
                '-x16', '-s16', '--summary-interval=4', 
                '--file-allocation=none', '--auto-file-renaming=false', url
            ]);

            let lastUpdate = 0;
            let fileName = "Menganalisa...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
                if (nameMatch && (fileName === "Menganalisa..." || !fileName)) fileName = nameMatch[1].trim();

                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const now = Date.now();
                    if (now - lastUpdate > 5000) {
                        lastUpdate = now;
                        await this._editTelegramMessage(`⬇️ **Aria2c Progress**\n\n📄 File: \`${fileName}\`\n📊 Status: \`${progressMatch[1]}%\`\n⚡ Speed: \`${progressMatch[2]}\``);
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selectors.json', 'package.json'].includes(f) && !f.endsWith('.png') && !f.endsWith('.aria2'));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error(`Aria2 failed code ${code}`));
            });
        });
    }

    // --- MAIN PROCESS ---
    async _processDefault() {
        let page = await this.context.newPage();
        
        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2 (Bypass Mode)...`);
            const downloadPromise = this.context.waitForEvent('download', { timeout: 60000 }).catch(() => null);

            try {
                await page.goto(this.url, { waitUntil: 'networkidle', timeout: 90000 });

                // --- BYPASS CLOUDFLARE TURNSTILE ---
                const cfIframe = page.locator('iframe[src*="challenges"]');
                if (await cfIframe.count() > 0) {
                    await this._editTelegramMessage(`🛡️ Cloudflare Terdeteksi! Menunggu verifikasi otomatis...`);
                    await page.waitForTimeout(10000); 
                    // Blind click ke arah checkbox biasanya
                    await page.mouse.click(150, 150).catch(() => {}); 
                }

                // Cek apakah halaman berubah (redirect)
                await page.waitForTimeout(5000);
                const pages = this.context.pages();
                page = pages[pages.length - 1];

                let actionDone = false;
                for (const selector of this.selectors) {
                    const btn = page.locator(selector).first();
                    if (await btn.isVisible()) {
                        const tag = await btn.evaluate(e => e.tagName.toLowerCase());
                        
                        if (tag === 'form') {
                            await this._editTelegramMessage(`📝 Men-submit Form...`);
                            await btn.evaluate(f => f.submit());
                        } else {
                            const href = await btn.getAttribute('href');
                            if (href && href.startsWith('http')) return await this._downloadWithAria2(href);
                            await this._editTelegramMessage(`🎯 Mengeklik Tombol...`);
                            await btn.click({ force: true });
                        }
                        actionDone = true;
                        break;
                    }
                }

                if (actionDone) {
                    const dl = await downloadPromise;
                    if (dl) {
                        const directUrl = dl.url();
                        await dl.cancel();
                        return await this._downloadWithAria2(directUrl);
                    }
                    await page.waitForLoadState('networkidle').catch(() => null);
                }
            } catch (e) { console.log(`Attempt ${attempt} gagal.`); }
        }
        
        await this._sendScreenshot(`❌ Gagal Total. Cloudflare atau selector tidak tembus.`);
        throw new Error("Gagal mendapatkan link download.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Engine Start (Headless: False)...**`);
        try {
            // BYPASS LOGIC: Headless False + Args Stealth
            this.browser = await chromium.launch({ 
                headless: false, 
                args: [
                    '--no-sandbox', 
                    '--disable-blink-features=AutomationControlled'
                ] 
            });

            // EMULASI MOBILE
            const mobile = devices['iPhone 13'];
            this.context = await this.browser.newContext({ ...mobile, acceptDownloads: true });
            
            const file = await this._processDefault();
            if (file) {
                const size = fs.statSync(file).size;
                fs.writeFileSync('downloaded_filename.txt', file);
                await this._editTelegramMessage(`✅ **Sukses!**\n📄 Nama: \`${file}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
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
