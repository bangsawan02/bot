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
        } catch (e) {
            console.error("Gagal baca selector.txt");
        }
        return [];
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
            const screenshotPath = 'debug.png';
            await page.screenshot({ path: screenshotPath, fullPage: true });
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, {
                headers: form.getHeaders()
            });
            fs.removeSync(screenshotPath);
        } catch (e) {}
    }

    _humanReadableSize(bytes) {
        if (!bytes) return "0B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

    // --- DOWNLOAD HANDLER ---
    async _handleDownload(download) {
        const filename = download.suggestedFilename();
        const savePath = path.join(process.cwd(), filename);
        
        let totalSize = null;
        try {
            const head = await axios.head(download.url(), { timeout: 8000 });
            totalSize = parseInt(head.headers['content-length']);
        } catch (e) {}

        await this._editTelegramMessage(`⬇️ **Downloading:** \`${filename}\``);

        let lastPercent = -10;
        const timer = setInterval(async () => {
            if (fs.existsSync(savePath) && totalSize) {
                const stats = fs.statSync(savePath);
                const percent = Math.floor((stats.size / totalSize) * 100);
                if (percent >= lastPercent + 10 && percent <= 100) {
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

    // --- CORE LOGIC ---
    async _processDefault() {
        const page = await this.context.newPage();
        page.setDefaultNavigationTimeout(60000);

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Menuju URL (Percobaan ${attempt}/2)...`);
            await page.goto(this.url, { waitUntil: 'networkidle', timeout: 60000 });

            // Pantau download selama seluruh sequence klik berjalan
            const downloadPromise = page.waitForEvent('download', { timeout: 60000 }).catch(() => null);

            for (const selector of this.selectors) {
                try {
                    const btn = page.locator(selector).first();
                    if (await btn.isVisible({ timeout: 10000 })) {
                        await this._editTelegramMessage(`🎯 Menekan: \`${selector}\``);
                        
                        // Klik dan tunggu navigasi/refresh selesai
                        await Promise.all([
                            page.waitForLoadState('networkidle').catch(() => null),
                            btn.click()
                        ]);
                        
                        // Jeda singkat agar data/selector baru muncul sempurna
                        await page.waitForTimeout(3000);
                        
                        // Cek apakah download terpancing setelah klik ini
                        // (Beberapa web butuh 1 klik, ada yang 2 klik)
                    }
                } catch (e) {
                    console.log(`Selector ${selector} tidak terlihat, lanjut...`);
                }
            }

            const download = await downloadPromise;
            if (download) return await this._handleDownload(download);

            if (attempt === 2) {
                await this._sendScreenshot(page, `❌ Gagal: Semua selector telah dicoba tapi download tidak muncul.`);
            }
        }
        throw new Error("Download gagal setelah 2x sequence.");
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
                        const files = fs.readdirSync('.').filter(f => !['main.js', 'selector.txt', 'package.json'].includes(f) && !f.endsWith('.png'));
                        code === 0 ? res(files[0]) : rej(new Error("MegaTools error"));
                    });
                });
            } else if (this.url.includes("pixeldrain.com")) {
                const id = this.url.split('/').pop();
                const info = await axios.get(`https://pixeldrain.com/api/file/${id}/info`);
                finalFile = info.data.name;
                await new Promise((res, rej) => {
                    spawn('aria2c', ['-x16', '-s16', '-o', finalFile, `https://pixeldrain.com/api/file/${id}?download`])
                        .on('close', (c) => c === 0 ? res() : rej());
                });
            } else {
                this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
                this.context = await this.browser.newContext({ acceptDownloads: true });
                finalFile = await this._processDefault();
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
