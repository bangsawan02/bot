const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const { spawn } = require('child_process');
const FormData = require('form-data');
const { URL } = require('url');

class SourceForgeBypass {
    constructor(url) {
        this.url = url;
        this.botToken = process.env.BOT_TOKEN;
        this.ownerId = process.env.OWNER_ID;
        this.initialMessageId = null;
        this.browser = null;
    }

    // --- TELEGRAM NOTIFIER ---
    async _notify(text) {
        if (!this.botToken || !this.ownerId) return;
        try {
            if (!this.initialMessageId) {
                const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
                    chat_id: this.ownerId, text, parse_mode: "Markdown"
                });
                this.initialMessageId = res.data.result.message_id;
            } else {
                await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                    chat_id: this.ownerId, message_id: this.initialMessageId, text, parse_mode: "Markdown"
                }).catch(() => {});
            }
        } catch (e) { console.error("Telegram Error:", e.message); }
    }

    async _sendScreenshot(caption) {
        if (!this.browser) return;
        try {
            const page = this.browser.contexts()[0].pages().pop();
            const path = 'sf_debug.png';
            await page.screenshot({ path });
            const form = new FormData();
            form.append('chat_id', this.ownerId);
            form.append('caption', caption);
            form.append('photo', fs.createReadStream(path));
            await axios.post(`https://api.telegram.org/bot${this.botToken}/sendPhoto`, form, { headers: form.getHeaders() });
            fs.removeSync(path);
        } catch (e) {}
    }

    // --- LOGIKA MANIPULASI URL MIRROR ---
    _generateMirrorUrl(targetUrl) {
        const parsed = new URL(targetUrl);
        const pathParts = parsed.pathname.split('/');
        // Format: /projects/PROJECTNAME/files/PATH/TO/FILE/download
        const projectName = pathParts[2];
        const filePath = pathParts.slice(4, -1).join('/');
        
        // Buat URL pilihan mirror
        return `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}`;
    }

    // --- ARIA2C ENGINE (MULTIPLE SOURCES) ---
    async _downloadWithAria2(urls, fileName) {
        await this._notify(`🚀 **Aria2c:** Mengunduh dari ${urls.length} mirror sekaligus...`);
        
        return new Promise((resolve, reject) => {
            // Kita masukkan semua URL mirror agar Aria2c mencari yang paling cepat/tembus
            const args = [
                '-x16', '-s16', '-j16', 
                '--summary-interval=5',
                '--file-allocation=none',
                '--check-certificate=false',
                '-o', fileName,
                ...urls // Tambahkan semua link mirror ke argumen
            ];

            const aria = spawn('aria2c', args);

            aria.stdout.on('data', (data) => {
                const out = data.toString();
                const progress = out.match(/\((.*)%\).*DL:(.*)\]/);
                if (progress) {
                    this._notify(`⬇️ **SourceForge Progress**\n📄 File: \`${fileName}\`\n📊 Progress: \`${progress[1]}%\`\n⚡ Speed: \`${progress[2]}\``);
                }
            });

            aria.on('close', (code) => {
                if (code === 0) resolve(fileName);
                else reject(new Error(`Aria2 exit code: ${code}`));
            });
        });
    }

    // --- MAIN EXECUTION ---
    async run() {
        await this._notify(`⏳ **Bypass SourceForge Dimulai...**`);
        
        try {
            this.browser = await chromium.launch({ 
                headless: false, 
                args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'] 
            });
            const context = await this.browser.newContext({ ...devices['iPhone 13'] });
            const page = await context.newPage();

            // 1. Dapatkan Nama File Asli
            await page.goto(this.url, { waitUntil: 'domcontentloaded' });
            const fileName = await page.locator('.file-info .dark-text, #downloading .content .file-info div').first().innerText()
                .catch(() => "downloaded_file");
            
            const cleanFileName = fileName.trim().replace(/\s+/g, '_');

            // 2. Lompat ke Halaman Mirror Choices
            const mirrorChoicesUrl = this._generateMirrorUrl(this.url);
            await this._notify(`🔎 Mengambil daftar mirror dari:\n\`${mirrorChoicesUrl}\``);
            
            await page.goto(mirrorChoicesUrl, { waitUntil: 'networkidle' });

            // 3. Ekstrak Semua ID Mirror
            const mirrorIds = await page.locator('ul#mirrorList li').evaluateAll(list => 
                list.map(li => li.id).filter(id => id)
            );

            if (mirrorIds.length === 0) {
                await this._sendScreenshot("Gagal menemukan list mirror.");
                throw new Error("Daftar mirror kosong. Mungkin terblokir Cloudflare.");
            }

            // 4. Bangun URL Direct untuk setiap mirror
            // Format: https://downloads.sourceforge.net/project/PROJECT/PATH?use_mirror=MIRRORID
            const baseDownloadUrl = this.url.replace('/download', '');
            const finalUrls = mirrorIds.map(mId => `${baseDownloadUrl}?use_mirror=${mId}`);

            // 5. Eksekusi Aria2c
            const successFile = await this._downloadWithAria2(finalUrls, cleanFileName);
            
            if (successFile) {
                const stats = fs.statSync(successFile);
                await this._notify(`✅ **Download Sukses!**\n📄 Nama: \`${successFile}\`\n⚖️ Size: \`${(stats.size/1024/1024).toFixed(2)} MB\``);
            }

        } catch (e) {
            await this._notify(`❌ **Error:** ${e.message}`);
            await this._sendScreenshot(`Debug: ${e.message}`);
        } finally {
            if (this.browser) await this.browser.close();
        }
    }
}

const target = process.env.PAYLOAD_URL || process.argv[2];
if (target) new SourceForgeBypass(target).run();
