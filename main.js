const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
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
        } catch (e) { }
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

    // --- ARIA2C ENGINE ---
    async _downloadWithAria2(url) {
        if (!url) return null;

        const isSourceForge = url.includes('sourceforge.net');
        const connectionLimit = isSourceForge ? '1' : '16';
        
        await this._editTelegramMessage(
            isSourceForge 
            ? `🎯 **Direct Hit SourceForge!**\nMenggunakan Aria2c (1 Koneksi)...` 
            : `🚀 **Aria2c:** Mendownload file...`
        );
        
        return new Promise((resolve, reject) => {
            const ariaArgs = [
                `-x${connectionLimit}`, 
                `-s${connectionLimit}`, 
                '--summary-interval=5', 
                '--file-allocation=none', 
                '--auto-file-renaming=false',
                '--allow-overwrite=true',
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                '--header=Referer: https://sourceforge.net/',
                url
            ];

            const aria = spawn('aria2c', ariaArgs);

            let lastUpdate = 0;
            let fileName = "Mengunduh...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                
                // Tangkap nama file
                const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
                if (nameMatch && (fileName === "Mengunduh..." || fileName === "Mengidentifikasi...")) {
                    fileName = nameMatch[1].trim();
                }

                // Tangkap progress
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
                } else {
                    reject(new Error(`Aria2 error code: ${code}`));
                }
            });
        });
    }

    // --- PLAYWRIGHT ENGINE (FALLBACK) ---
    async _processPlaywright() {
        await this._editTelegramMessage(`🎭 **Playwright Mode:** Mencari link download tersembunyi...`);
        
        this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
        this.context = await this.browser.newContext({ ...devices['Desktop Chrome'], acceptDownloads: true });
        let page = await this.context.newPage();

        const downloadPromise = this.context.waitForEvent('download', { timeout: 60000 }).catch(() => null);

        try {
            await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
            await page.waitForTimeout(5000); // Tunggu trigger otomatis

            // Jika tidak ada download otomatis, coba klik manual berdasarkan selector
            for (const selector of this.selectors) {
                try {
                    const el = page.locator(selector).first();
                    if (await el.isVisible({ timeout: 3000 })) {
                        await el.click({ force: true });
                        break;
                    }
                } catch (e) { continue; }
            }

            const dlObj = await downloadPromise;
            if (dlObj) {
                const directUrl = dlObj.url();
                await dlObj.cancel();
                await this.browser.close();
                return await this._downloadWithAria2(directUrl);
            }
        } catch (e) {
            if (this.browser) await this.browser.close();
            throw e;
        }
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Memulai Proses...**\n🌐 URL: \`${this.url}\``);

        // LOGIKA BARU: Jika SourceForge, langsung tembak!
        if (this.url.includes('sourceforge.net')) {
            try {
                const file = await this._downloadWithAria2(this.url);
                if (file) return this._finish(file);
            } catch (e) {
                await this._editTelegramMessage(`⚠️ Tembakan langsung gagal (403/Error). Mencoba via Playwright...`);
            }
        }

        // Jalankan Playwright jika bukan SF atau jika tembakan langsung SF gagal
        try {
            const file = await this._processPlaywright();
            if (file) return this._finish(file);
        } catch (e) {
            await this._editTelegramMessage(`❌ **Misi Gagal:** ${e.message}`);
            process.exit(1);
        }
    }

    _finish(fileName) {
        const size = fs.statSync(fileName).size;
        fs.writeFileSync('downloaded_filename.txt', fileName);
        this._editTelegramMessage(`✅ **Berhasil Didownload!**\n📄 File: \`${fileName}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
