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
        } catch (e) {}
        return ['#downloadButton', '.btn-download', 'a[href*="download"]'];
    }

    // --- TELEGRAM HELPERS ---
    async _sendTelegramMessage(text) {
        if (!this.botToken || !this.ownerId) return;
        try {
            const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
                chat_id: this.ownerId,
                text: text,
                parse_mode: "Markdown"
            });
            this.initialMessageId = res.data.result.message_id;
        } catch (e) {}
    }

    async _editTelegramMessage(text) {
        if (!this.initialMessageId) return;
        try {
            await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                chat_id: this.ownerId,
                message_id: this.initialMessageId,
                text: text,
                parse_mode: "Markdown"
            });
        } catch (e) {}
    }

    async _sendScreenshot(page, caption) {
        try {
            const screenshotPath = 'error_debug.png';
            await page.screenshot({ path: screenshotPath, fullPage: true });
            
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));

            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, {
                headers: form.getHeaders()
            });
            fs.removeSync(screenshotPath);
        } catch (e) {
            console.error("Gagal kirim screenshot:", e.message);
        }
    }

    _humanReadableSize(bytes) {
        if (!bytes) return "0B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

    // --- CORE LOGIC ---
    async _handleDownload(download) {
        const filename = download.suggestedFilename();
        const savePath = path.join(process.cwd(), filename);
        
        let totalSize = null;
        try {
            const head = await axios.head(download.url(), { timeout: 5000 });
            totalSize = parseInt(head.headers['content-length']);
        } catch (e) {}

        await this._editTelegramMessage(`⬇️ **Downloading:** \`${filename}\``);

        let lastPercent = -10;
        const timer = setInterval(async () => {
            if (fs.existsSync(savePath) && totalSize) {
                const stats = fs.statSync(savePath);
                const percent = Math.floor((stats.size / totalSize) * 100);
                if (percent >= lastPercent + 10) {
                    lastPercent = percent;
                    await this._editTelegramMessage(`⬇️ **Progress:** \`${percent}%\` (\`${this._humanReadableSize(stats.size)}\` / \`${this._humanReadableSize(totalSize)}\`)`);
                }
            }
        }, 4000);

        await download.saveAs(savePath);
        clearInterval(timer);
        await this._editTelegramMessage(`✅ **Selesai:** \`${filename}\` (\`${this._humanReadableSize(fs.statSync(savePath).size)}\`)`);
        return filename;
    }

    async _processDefault() {
        const page = await this.context.newPage();
        await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        // BATASI 2x PERCOBAAN SAJA
        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`📡 Mencoba mencari tombol... (Percobaan ${attempt}/2)`);
            
            const downloadPromise = page.waitForEvent('download', { timeout: 20000 }).catch(() => null);

            let clicked = false;
            for (const selector of this.selectors) {
                try {
                    const btn = page.locator(selector).first();
                    if (await btn.isVisible({ timeout: 4000 })) {
                        await btn.click();
                        clicked = true;
                        break;
                    }
                } catch (e) {}
            }

            const download = await downloadPromise;
            if (download) return await this._handleDownload(download);

            if (!clicked && attempt === 2) {
                await this._sendScreenshot(page, `❌ Gagal: Tidak ada selector yang cocok setelah 2x percobaan.`);
            }
            await page.waitForTimeout(3000);
        }
        throw new Error("Gagal mendownload setelah 2 percobaan.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Memproses:** \`${this.url}\``);
        let finalFile = null;

        try {
            if (this.url.includes("mega.nz")) {
                // Logika Megatools tetap sama
                finalFile = await new Promise((res, rej) => {
                    const mega = spawn('megatools', ['dl', this.url]);
                    mega.on('close', (c) => {
                        const f = fs.readdirSync('.').find(x => !x.endsWith('.js') && !x.endsWith('.txt'));
                        c === 0 ? res(f) : rej(new Error("Mega Error"));
                    });
                });
            } else {
                // DEFAULT PLAYWRIGHT
                this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
                this.context = await this.browser.newContext({ acceptDownloads: true });
                finalFile = await this._processDefault();
            }

            if (finalFile) fs.writeFileSync('downloaded_filename.txt', finalFile);
        } catch (e) {
            await this._editTelegramMessage(`❌ **Stop:** ${e.message}`);
            process.exit(1);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new DownloaderBot(target).run();
