const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');

// Pasang plugin stealth agar tidak terdeteksi bot
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

// =========================================================
// CLASS UTAMA: DownloaderBot
// =========================================================
class DownloaderBot {
    constructor(url) {
        this.url = url;
        this.botToken = process.env.BOT_TOKEN;
        this.ownerId = process.env.OWNER_ID || process.env.PAYLOAD_SENDER;
        this.initialMessageId = null;
        this.browser = null;
        this.context = null;
        this.selectors = this._loadSelectors();
    }

    // Mengambil daftar selector dari file eksternal
    _loadSelectors() {
        try {
            if (fs.existsSync('selector.txt')) {
                return fs.readFileSync('selector.txt', 'utf-8')
                    .split('\n')
                    .map(s => s.trim())
                    .filter(s => s.length > 0);
            }
        } catch (e) {
            console.error("Gagal membaca selector.txt:", e.message);
        }
        return ['#downloadButton', '.download-btn', 'a[href*="download"]']; // Default fallback
    }

    // =========================================================
    // 1. TELEGRAM HELPER
    // =========================================================
    _humanReadableSize(sizeBytes) {
        if (!sizeBytes || sizeBytes === 0) return "0B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        const i = Math.floor(Math.log(sizeBytes) / Math.log(1024));
        return (sizeBytes / Math.pow(1024, i)).toFixed(2) + " " + units[i];
    }

    async _sendTelegramMessage(text) {
        if (!this.botToken || !this.ownerId) return console.log("TG:", text);
        try {
            const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
                chat_id: this.ownerId,
                text: text,
                parse_mode: "Markdown"
            });
            this.initialMessageId = res.data.result.message_id;
        } catch (e) { console.error("TG Error:", e.message); }
    }

    async _editTelegramMessage(text) {
        if (!this.botToken || !this.ownerId || !this.initialMessageId) return;
        try {
            await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                chat_id: this.ownerId,
                message_id: this.initialMessageId,
                text: text,
                parse_mode: "Markdown"
            });
        } catch (e) {}
    }

    // =========================================================
    // 2. LOGIKA DOWNLOAD KHUSUS (MEGA & PIXELDRAIN)
    // =========================================================
    async _downloadWithAria2(url, filename) {
        return new Promise((resolve, reject) => {
            const args = ['-x', '16', '-s', '16', '-o', filename, url];
            const aria = spawn('aria2c', args);
            aria.on('close', (code) => code === 0 ? resolve(filename) : reject(`Aria2 error ${code}`));
        });
    }

    async _downloadWithMega(url) {
        return new Promise((resolve, reject) => {
            const mega = spawn('megatools', ['dl', url]);
            mega.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => !f.endsWith('.json') && f !== 'main.js');
                    resolve(files[0]);
                } else reject(`Mega error ${code}`);
            });
        });
    }

    // =========================================================
    // 3. LOGIKA DEFAULT DOWNLOADER (PLAYWRIGHT)
    // =========================================================
    async _initializeBrowser() {
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            return true;
        } catch (e) { return false; }
    }

    async _processDefaultDownload() {
        const page = await this.context.newPage();
        await this._editTelegramMessage(`🔎 Menuju: ${this.url}`);
        await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        let downloadedFile = null;

        // Loop maksimal 3 percobaan jika terjadi redirect
        for (let attempt = 1; attempt <= 3; attempt++) {
            await this._editTelegramMessage(`📡 Mencari tombol... (Percobaan ${attempt}/3)`);

            // Listener untuk menangkap event download
            const downloadPromise = page.waitForEvent('download', { timeout: 30000 }).catch(() => null);

            let clickSuccess = false;
            for (const selector of this.selectors) {
                try {
                    const btn = page.locator(selector).first();
                    if (await btn.isVisible({ timeout: 3000 })) {
                        await this._editTelegramMessage(`🎯 Klik: \`${selector}\``);
                        await btn.click();
                        clickSuccess = true;
                        break;
                    }
                } catch (e) {}
            }

            const download = await downloadPromise;
            if (download) {
                const filename = download.suggestedFilename();
                const savePath = path.join(process.cwd(), filename);
                await this._editTelegramMessage(`⬇️ Mendownload: \`${filename}\`...`);
                await download.saveAs(savePath);
                downloadedFile = filename;
                break;
            }

            if (!clickSuccess) {
                await page.waitForTimeout(5000); // Tunggu sebentar jika page loading/redirect
            }
        }

        if (!downloadedFile) throw new Error("Gagal: File tidak ditemukan/terdownload.");
        return downloadedFile;
    }

    // =========================================================
    // 4. MAIN RUNNER
    // =========================================================
    async run() {
        await this._sendTelegramMessage(`⏳ **Memproses URL...**`);
        let finalFile = null;

        try {
            if (this.url.includes("mega.nz")) {
                finalFile = await this._downloadWithMega(this.url);
            } else if (this.url.includes("pixeldrain.com")) {
                const id = this.url.split('/').pop();
                const info = await axios.get(`https://pixeldrain.com/api/file/${id}/info`);
                finalFile = await this._downloadWithAria2(`https://pixeldrain.com/api/file/${id}?download`, info.data.name);
            } else {
                if (await this._initializeBrowser()) {
                    finalFile = await this._processDefaultDownload();
                }
            }

            if (finalFile) {
                const size = this._humanReadableSize(fs.statSync(finalFile).size);
                await this._editTelegramMessage(`✅ **Selesai!**\nFile: \`${finalFile}\`\nSize: \`${size}\``);
                fs.writeFileSync('downloaded_filename.txt', finalFile);
            }
        } catch (e) {
            await this._editTelegramMessage(`❌ **Error:** ${e.message}`);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

// Eksekusi
const targetUrl = process.env.PAYLOAD_URL || process.argv[2];
if (targetUrl) {
    new DownloaderBot(targetUrl).run();
} else {
    console.error("URL tidak ditemukan!");
}
