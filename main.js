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
            return ["form", "a:has-text('Download')", "button:has-text('Download')"];
        }
    }

    // Mengubah bytes ke format yang enak dibaca (MB/GB)
    _humanSize(bytes) {
        if (!bytes || bytes === 0) return "0 B";
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB", "TB"][i];
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

    // --- ARIA2C ENGINE DENGAN LIVE PROGRESS ---
    async _downloadWithAria2(url) {
        await this._editTelegramMessage(`🚀 **Aria2c:** Memulai koneksi...`);
        return new Promise((resolve, reject) => {
            const aria = spawn('aria2c', ['-x16', '-s16', '--summary-interval=3', '--console-log-level=notice', url]);
            let lastUpdate = 0;
            let fileName = "Sedang mengambil nama file...";

            aria.stdout.on('data', async (data) => {
                const output = data.toString();
                
                // Cari nama file dari log aria2
                const nameMatch = output.match(/Saving to: (.+)/);
                if (nameMatch) fileName = path.basename(nameMatch[1]);

                // Cari persentase dan kecepatan
                const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
                if (progressMatch) {
                    const percent = progressMatch[1];
                    const speed = progressMatch[2];
                    const now = Date.now();
                    if (now - lastUpdate > 4000) { // Update tiap 4 detik agar tidak kena spam limit
                        lastUpdate = now;
                        await this._editTelegramMessage(`⬇️ **Aria2c Downloading...**\n\n📄 File: \`${fileName}\`\n📊 Progress: \`${percent}%\`\n⚡ Speed: \`${speed}\``);
                    }
                }
            });

            aria.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync('.').filter(f => !['main.js', 'selectors.json', 'package.json'].includes(f) && !f.endsWith('.png') && !f.endsWith('.aria2'));
                    const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                    resolve(sorted[0].n);
                } else reject(new Error(`Aria2 Error Code ${code}`));
            });
        });
    }

    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Memindai halaman...`);
            const pages = this.context.pages();
            currentPage = pages[pages.length - 1];
            
            try {
                await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => null);
                await currentPage.waitForTimeout(4000); 

                const downloadPromise = this.context.waitForEvent('download', { timeout: 20000 }).catch(() => null);
                let actionDone = false;

                for (const selector of this.selectors) {
                    try {
                        const element = currentPage.locator(selector).first();
                        await element.waitFor({ state: 'attached', timeout: 5000 });
                        const tagName = await element.evaluate(el => el.tagName.toLowerCase());

                        if (tagName === 'form') {
                            await this._editTelegramMessage(`📝 Submit Form...`);
                            await element.evaluate(form => form.submit());
                            actionDone = true;
                        } else {
                            const href = await element.getAttribute('href');
                            if (href && href.startsWith('http') && !href.includes('javascript:')) {
                                return await this._downloadWithAria2(href);
                            }
                            await this._editTelegramMessage(`🎯 Klik Tombol: \`${selector}\``);
                            await element.click({ force: true });
                            actionDone = true;
                        }
                        if (actionDone) break;
                    } catch (e) { continue; }
                }

                if (actionDone) {
                    const download = await downloadPromise;
                    if (download) {
                        const filename = download.suggestedFilename();
                        await download.saveAs(filename);
                        return filename;
                    }
                    await currentPage.waitForLoadState('networkidle').catch(() => null);
                }
            } catch (e) { console.log(e.message); }
        }
        throw new Error("Gagal: File tidak ditemukan atau timeout.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot dimulai...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            
            const finalFile = await this._processDefault();
            
            if (finalFile && fs.existsSync(finalFile)) {
                // LOGIKA BARU: Ambil ukuran file
                const stats = fs.statSync(finalFile);
                const sizeStr = this._humanSize(stats.size);
                
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n\n📄 Nama: \`${finalFile}\`\n⚖️ Ukuran: \`${sizeStr}\``);
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
