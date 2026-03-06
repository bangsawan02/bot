const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

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

    _loadSelectors() {
        try {
            if (fs.existsSync('selector.txt')) {
                return fs.readFileSync('selector.txt', 'utf-8')
                    .split('\n')
                    .map(s => s.trim())
                    .filter(s => s.length > 0);
            }
        } catch (e) { console.error("Gagal baca selector.txt:", e.message); }
        return ['#downloadButton', '.download-btn', 'a[href*="download"]'];
    }

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

    async _initializeBrowser() {
        try {
            this.browser = await chromium.launch({ 
                headless: true, 
                args: ['--no-sandbox', '--disable-setuid-sandbox'] 
            });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            return true;
        } catch (e) { return false; }
    }

    async _handleDownloadProgress(download) {
        const filename = download.suggestedFilename();
        const savePath = path.join(process.cwd(), filename);
        
        let totalSize = null;
        try {
            // Coba ambil ukuran file dari HEAD request
            const head = await axios.head(download.url(), { timeout: 8000 });
            totalSize = parseInt(head.headers['content-length']);
        } catch (e) { console.log("Info: Total size tidak ditemukan via HEAD"); }

        await this._editTelegramMessage(`⬇️ **Download Dimulai:** \`${filename}\``);

        let lastPercent = -10; // Trigger update pertama kali
        const progressTimer = setInterval(async () => {
            if (fs.existsSync(savePath) && totalSize) {
                const stats = fs.statSync(savePath);
                const percent = Math.floor((stats.size / totalSize) * 100);

                // Update tiap naik 10% agar tidak kena spam-limit Telegram
                if (percent >= lastPercent + 10 && percent <= 100) {
                    lastPercent = percent;
                    const loaded = this._humanReadableSize(stats.size);
                    const total = this._humanReadableSize(totalSize);
                    await this._editTelegramMessage(`⬇️ **Downloading:** \`${filename}\`\n📊 **Progress:** \`${percent}%\` (\`${loaded}\` / \`${total}\`)`);
                }
            }
        }, 4000);

        try {
            await download.saveAs(savePath);
            clearInterval(progressTimer);
            const finalStats = fs.statSync(savePath);
            await this._editTelegramMessage(`✅ **Selesai!**\nFile: \`${filename}\` (\`${this._humanReadableSize(finalStats.size)}\`)`);
            return filename;
        } catch (err) {
            clearInterval(progressTimer);
            throw err;
        }
    }

    async _processDefaultDownload() {
        const page = await this.context.newPage();
        await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        for (let attempt = 1; attempt <= 3; attempt++) {
            await this._editTelegramMessage(`📡 Mencari tombol... (Percobaan ${attempt}/3)`);
            const downloadPromise = page.waitForEvent('download', { timeout: 30000 }).catch(() => null);

            let clicked = false;
            for (const selector of this.selectors) {
                try {
                    const btn = page.locator(selector).first();
                    if (await btn.isVisible({ timeout: 3000 })) {
                        await btn.click();
                        clicked = true;
                        break;
                    }
                } catch (e) {}
            }

            const download = await downloadPromise;
            if (download) return await this._handleDownloadProgress(download);
            if (!clicked) await page.waitForTimeout(5000);
        }
        throw new Error("Gagal: Tidak ada download terdeteksi.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Memproses:** \`${this.url}\``);
        let finalFile = null;

        try {
            if (this.url.includes("mega.nz")) {
                await this._editTelegramMessage("⬇️ **MEGA Mode...**");
                finalFile = await new Promise((res, rej) => {
                    const mega = spawn('megatools', ['dl', this.url]);
                    mega.on('close', (code) => {
                        if (code === 0) {
                            const files = fs.readdirSync('.').filter(f => !f.endsWith('.js') && !f.endsWith('.txt') && !f.endsWith('.json'));
                            res(files[0]);
                        } else rej(new Error("Megatools gagal"));
                    });
                });
            } else if (this.url.includes("pixeldrain.com")) {
                const id = this.url.split('/').pop();
                const info = await axios.get(`https://pixeldrain.com/api/file/${id}/info`);
                finalFile = info.data.name;
                const args = ['-x', '16', '-s', '16', '-o', finalFile, `https://pixeldrain.com/api/file/${id}?download`];
                await new Promise((res, rej) => {
                    spawn('aria2c', args).on('close', (c) => c === 0 ? res() : rej());
                });
            } else {
                if (await this._initializeBrowser()) finalFile = await this._processDefaultDownload();
            }

            if (finalFile) fs.writeFileSync('downloaded_filename.txt', finalFile);
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
