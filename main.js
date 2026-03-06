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
        } catch (e) { console.log("Selector.txt tidak ditemukan, menggunakan fallback."); }
        return ['a[href*="download"]', 'button:has-text("Download")', '.download-btn'];
    }

    // --- TELEGRAM LOGIC ---
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
            const screenshotPath = 'debug_fail.png';
            await page.screenshot({ path: screenshotPath, fullPage: true });
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(screenshotPath));
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, {
                headers: form.getHeaders()
            });
            fs.removeSync(screenshotPath);
        } catch (e) { console.error("Gagal kirim screenshot:", e.message); }
    }

    _humanReadableSize(bytes) {
        if (!bytes) return "0B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

    // --- DOWNLOAD ENGINES ---
    async _downloadWithAria2(url, filename = "") {
        await this._editTelegramMessage(`🚀 **Aria2c Engine:** Mendownload direct link...`);
        return new Promise((resolve, reject) => {
            const args = ['-x', '16', '-s', '16', '--summary-interval=0', '--console-log-level=warn', url];
            if (filename) args.push('-o', filename);
            
            const aria = spawn('aria2c', args);
            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selector.txt', 'package.json'].includes(f) && !f.endsWith('.png'));
                    // Ambil file terbaru yang bukan script
                    const sortedFiles = files.map(f => ({ name: f, time: fs.statSync(f).mtime })).sort((a, b) => b.time - a.time);
                    resolve(sortedFiles[0].name);
                } else reject(new Error(`Aria2 Error Code: ${code}`));
            });
        });
    }

    async _handlePlaywrightDownload(download) {
        const filename = download.suggestedFilename();
        const savePath = path.join(process.cwd(), filename);
        await this._editTelegramMessage(`⬇️ **Playwright Engine:** Menyimpan \`${filename}\`...`);
        await download.saveAs(savePath);
        return filename;
    }

    // --- MAIN BROWSER PROCESS ---
    async _processDefault() {
        const page = await this.context.newPage();
        page.setDefaultNavigationTimeout(60000);

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Menuju URL (Percobaan ${attempt}/2)...`);
            await page.goto(this.url, { waitUntil: 'networkidle', timeout: 60000 });

            // Pantau download selama interaksi
            const downloadPromise = page.waitForEvent('download', { timeout: 45000 }).catch(() => null);

            for (const selector of this.selectors) {
                try {
                    const btn = page.locator(selector).first();
                    await btn.waitFor({ state: 'attached', timeout: 8000 });

                    // 1. CEK APAKAH ADA HREF (DIRECT LINK)
                    const href = await btn.getAttribute('href');
                    if (href && href.startsWith('http') && !href.includes('javascript:')) {
                        await this._editTelegramMessage(`🔗 Direct Link terdeteksi pada \`${selector}\`.`);
                        return await this._downloadWithAria2(href);
                    }

                    // 2. JIKA TIDAK ADA HREF, PAKSA KLIK
                    await btn.scrollIntoViewIfNeeded().catch(() => null);
                    await this._editTelegramMessage(`🎯 Menekan: \`${selector}\`...`);
                    
                    await Promise.all([
                        page.waitForLoadState('networkidle').catch(() => null),
                        btn.click({ force: true })
                    ]);
                    
                    await page.waitForTimeout(4000); // Jeda refresh/render
                } catch (e) {
                    console.log(`[Log] ${selector} tidak siap/tidak ada.`);
                }
            }

            const download = await downloadPromise;
            if (download) return await this._handlePlaywrightDownload(download);

            if (attempt === 2) {
                await this._sendScreenshot(page, `❌ Gagal: Download tidak terpancing setelah semua klik.`);
            }
        }
        throw new Error("Gagal mendapatkan file setelah 2x percobaan.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Target:** \`${this.url}\``);
        let finalFile = null;

        try {
            if (this.url.includes("mega.nz")) {
                await this._editTelegramMessage("⬇️ **MEGA Engine Active...**");
                finalFile = await new Promise((res, rej) => {
                    const mega = spawn('megatools', ['dl', this.url]);
                    mega.on('close', (c) => {
                        const files = fs.readdirSync('.').filter(f => !['main.js', 'selector.txt', 'package.json'].includes(f));
                        c === 0 ? res(files[0]) : rej(new Error("Megatools Gagal."));
                    });
                });
            } else {
                this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
                this.context = await this.browser.newContext({ acceptDownloads: true });
                finalFile = await this._processDefault();
            }

            if (finalFile) {
                const stats = fs.statSync(finalFile);
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n📄 File: \`${finalFile}\`\n⚖️ Size: \`${this._humanReadableSize(stats.size)}\``);
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
