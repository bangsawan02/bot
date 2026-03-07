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

    _humanSize(bytes) {
        if (!bytes || bytes === 0) return "Unknown";
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
            }).catch(() => {}); // Abaikan error "message is not modified"
        } catch (e) {}
    }

// --- ARIA2C WITH PROGRESS TRACKER (FIXED) ---
async _downloadWithAria2(url) {
    await this._editTelegramMessage(`🚀 **Aria2c:** Memulai koneksi...`);
    
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
        let fileName = "Mengunduh...";

        aria.stdout.on('data', async (data) => {
            const output = data.toString();

            // Perbaikan Regex untuk menangkap nama file lebih akurat
            const nameMatch = output.match(/Saving to: .*\/(.+)/) || output.match(/Saving to: (.+)/);
            if (nameMatch && (fileName === "Mengunduh..." || fileName === undefined)) {
                fileName = nameMatch[1].trim();
            }

            const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);
            if (progressMatch) {
                const percent = progressMatch[1];
                const speed = progressMatch[2];
                
                const now = Date.now();
                if (now - lastUpdate > 4000) {
                    lastUpdate = now;
                    // Pastikan fileName bukan undefined sebelum diedit ke Telegram
                    const safeFileName = fileName || "Unknown File";
                    const status = `⬇️ **Aria2c Progress**\n\n📄 File: \`${safeFileName}\`\n📊 Progress: \`${percent}%\`\n⚡ Speed: \`${speed}\``;
                    await this._editTelegramMessage(status);
                }
            }
        });

        aria.on('close', (code) => {
            if (code === 0) {
                // Mencari file asli dengan filter yang lebih ketat
                const files = fs.readdirSync('.').filter(f => 
                    !['main.js', 'selectors.json', 'package.json', 'downloaded_filename.txt'].includes(f) && 
                    !f.endsWith('.png') && !f.endsWith('.aria2')
                );
                
                if (files.length === 0) return reject(new Error("File tidak ditemukan setelah download selesai."));
                
                const sorted = files.map(f => ({ n: f, t: fs.statSync(f).mtime })).sort((a, b) => b.t - a.t);
                resolve(sorted[0].n);
            } else {
                reject(new Error(`Aria2 berhenti dengan kode ${code}`));
            }
        });
    });
}

    // --- MAIN PROCESS ---
    async _processDefault() {
        let currentPage = await this.context.newPage();
        
        await this.context.route('**/*', (route) => {
            const u = route.request().url();
            if (['google-analytics', 'adskeeper', 'popads'].some(d => u.includes(d))) return route.abort();
            route.continue();
        });

        await currentPage.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });

        for (let attempt = 1; attempt <= 2; attempt++) {
            await this._editTelegramMessage(`🔎 Percobaan ${attempt}/2: Memindai...`);
            
            const pages = this.context.pages();
            currentPage = pages[pages.length - 1];
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
                        await this._editTelegramMessage(`🎯 Klik Tombol...`);
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
                    await this._editTelegramMessage(`⬇️ **Playwright Download:** \`${filename}\``);
                    await download.saveAs(filename);
                    return filename;
                }
                await currentPage.waitForLoadState('networkidle').catch(() => null);
            }
        }
        throw new Error("Gagal mendapatkan file.");
    }

    async run() {
        await this._sendTelegramMessage(`⏳ **Bot Memulai...**`);
        try {
            this.browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
            this.context = await this.browser.newContext({ acceptDownloads: true });
            const finalFile = await this._processDefault();
            if (finalFile) {
                const size = fs.statSync(finalFile).size;
                fs.writeFileSync('downloaded_filename.txt', finalFile);
                await this._editTelegramMessage(`✅ **Selesai!**\n📄 Name: \`${finalFile}\`\n⚖️ Size: \`${this._humanSize(size)}\``);
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
