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
        this.selectors = this._loadSelectorsByDomain();
    }

    _loadSelectorsByDomain() {
        try {
            const data = fs.readJsonSync('selectors.json');
            const domain = new URL(this.url).hostname.replace('www.', '');
            return data[domain] || data['default'];
        } catch (e) {
            return ["a[href^='http']", "form", "button:has-text('Download')"];
        }
    }

    _humanSize(bytes) {
        if (!bytes || bytes === 0) return "Unknown";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
    }

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

    async _downloadWithAria2(url) {
        if (!url) return null;
        await this._editTelegramMessage(`🚀 **Aria2c:** Menginisiasi link download...`);
        
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', [
                '-x16', '-s16', 
                '--summary-interval=3', 
                '--console-log-level=notice',
                '--file-allocation=none',
                '--auto-file-renaming=false',
                url
            ]);

            let lastUpdate = 0;
            let fileName = "Menganalisa file...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
                if (nameMatch && (fileName === "Menganalisa file..." || !fileName)) {
                    fileName = nameMatch[1].trim();
                }

                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const percent = progressMatch[1];
                    const speed = progressMatch[2];
                    const now = Date.now();
                    if (now - lastUpdate > 4000) {
                        lastUpdate = now;
                        const safeName = fileName || "Unknown File";
                        const status = `⬇️ **Aria2c Downloading**\n\n📄 File: \`${safeName}\`\n📊 Progress: \`${percent}%\`\n⚡ Speed: \`${speed}\``;
                        await this._editTelegramMessage(status);
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => 
                        !['main.js', 'selectors.json', 'package.json', 'downloaded_filename.txt'].includes(f) && 
                        !f.endsWith('.png') && !f.endsWith('.aria2')
                    );
                    if (files.length === 0) return reject(new Error("File tidak ditemukan."));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error(`Aria2 error code: ${code}`));
            });
        });
    }

    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        await this.context.route('**/*', (route) => {
            const u = route.request().url();
            if (['google-analytics', 'adskeeper', 'popads', 'doubleclick'].some(d => u.includes(d))) return route.abort();
            route.continue();
        });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Membuka Halaman...`);
            
            // 1. DENGARKAN AUTO-DOWNLOAD (UNTUK SOURCEFORGE DLL)
            const downloadPromise = this.context.waitForEvent('download', { timeout: 30000 }).catch(() => null);

            try {
                await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
                
                // Tunggu 8 detik untuk auto-download
                await this._editTelegramMessage(`⏳ Menunggu Auto-Download...`);
                const autoDl = await downloadPromise;
                if (autoDl) {
                    const dlUrl = autoDl.url();
                    await autoDl.cancel(); // Batalkan di Playwright, oper ke Aria2c
                    return await this._downloadWithAria2(dlUrl);
                }

                // 2. JIKA TIDAK ADA AUTO-DOWNLOAD, CARI SELECTOR / FORM
                await this._editTelegramMessage(`🎯 Mencari tombol/form download...`);
                const pages = this.context.pages();
                currentPage = pages[pages.length - 1]; // Fokus ke tab paling baru (jika ada pop-up)

                let actionDone = false;
                for (const selector of this.selectors) {
                    try {
                        const el = currentPage.locator(selector).first();
                        await el.waitFor({ state: 'attached', timeout: 5000 });

                        const tag = await el.evaluate(e => e.tagName.toLowerCase());

                        if (tag === 'form') {
                            await this._editTelegramMessage(`📝 Submit form pada \`${selector}\``);
                            await el.evaluate(f => f.submit());
                            actionDone = true;
                        } else {
                            const href = await el.getAttribute('href');
                            if (href && href.startsWith('http') && !href.includes('javascript:')) {
                                return await this._downloadWithAria2(href);
                            }
                            await this._editTelegramMessage(`🎯 Klik tombol \`${selector}\``);
                            await el.click({ force: true });
                            actionDone = true;
                        }
                        if (actionDone) break;
                    } catch (e) { continue; }
                }

                if (actionDone) {
                    // Cek lagi apakah setelah klik muncul download
                    const clickDl = await downloadPromise;
                    if (clickDl) {
                        const dlUrl = clickDl.url();
                        await clickDl.cancel();
                        return await this._downloadWithAria2(dlUrl);
                    }
                    // Jika tidak ada download, tunggu network idle sebelum lanjut attempt 2 (mungkin redirect)
                    await currentPage.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => null);
                }

            } catch (e) { console.log(`Attempt ${attempt} Error: ${e.message}`); }
        }
        throw new Error("Gagal: Tidak ada file yang terdeteksi.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Engine Start...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            
            if (finalFile) {
                const size = fs.statSync(finalFile).size;
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n📄 Nama: \`${finalFile}\`\n⚖️ Ukuran: \`${this._humanSize(size)}\``);
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
