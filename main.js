// Ganti require yang lama dengan ini:
const { chromium } = require('playwright-extra');
const stealth = require('stealth-plugin')();

// Tambahkan plugin stealth ke engine playwright
chromium.use(stealth);

// Sisanya sama, tapi saat launch browser gunakan chromium dari playwright-extra
const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');
const { URL } = require('url');
const querystring = require('querystring');

// =========================================================
// CLASS UTAMA: DownloaderBot
// =========================================================
class DownloaderBot {
    constructor(url) {
        this.url = url;
        this.botToken = process.env.BOT_TOKEN;
        this.ownerId = process.env.OWNER_ID || process.env.PAYLOAD_SENDER;
        
        // Buat folder temp unik
        this.tempDownloadDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl-bot-'));
        this.initialMessageId = null;
        this.browser = null;
        this.context = null;
    }

    async cleanup() {
        if (this.browser) await this.browser.close();
        try { await fs.remove(this.tempDownloadDir); } catch (e) {}
    }

    // =========================================================
    // 1. METODE BANTUAN TELEGRAM & UMUM
    // =========================================================
    _humanReadableSize(sizeBytes) {
        if (!sizeBytes || sizeBytes === 0) return "0B";
        const sizeName = ["B", "KB", "MB", "GB", "TB", "PB"];
        const i = Math.floor(Math.log(sizeBytes) / Math.log(1024));
        const p = Math.pow(1024, i);
        const s = (sizeBytes / p).toFixed(2);
        return `${s} ${sizeName[i]}`;
    }

    async _sendTelegramMessage(text) {
        if (!this.botToken || !this.ownerId) {
            console.log("TG LOG:", text);
            return null;
        }
        try {
            const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
                chat_id: this.ownerId,
                text: text,
                parse_mode: "Markdown"
            });
            this.initialMessageId = res.data.result.message_id;
            return this.initialMessageId;
        } catch (e) {
            console.error("Gagal kirim pesan TG:", e.message);
        }
    }

    async _editTelegramMessage(text) {
        if (!this.botToken || !this.ownerId || !this.initialMessageId) {
            console.log("TG EDIT LOG:", text);
            return;
        }
        try {
            await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
                chat_id: this.ownerId,
                message_id: this.initialMessageId,
                text: text,
                parse_mode: "Markdown"
            });
        } catch (e) { /* ignore edit errors to prevent spam logs */ }
    }

    async _getTotalFileSizeSafe(url) {
        try {
            const res = await axios.head(url, { maxRedirects: 5, timeout: 10000 });
            if (res.headers['content-length']) return parseInt(res.headers['content-length']);
        } catch (e) {}
        return null;
    }

    async _extractFilename(downloadUrl) {
        try {
            const res = await axios.head(downloadUrl, { maxRedirects: 5, timeout: 10000 });
            const cdHeader = res.headers['content-disposition'];
            if (cdHeader) {
                const match = cdHeader.match(/filename\*?=["']?(?:utf-8'')?([^"';]+)["']?/i);
                if (match) return match[1].trim().replace(/[^\x00-\x7F]/g, ""); // strip non-ascii
            }
        } catch (e) {}
        
        try {
            const parsed = new URL(downloadUrl);
            const fileName = path.basename(parsed.pathname);
            if (fileName) return fileName;
        } catch(e) {}
        
        return "unknown_file_" + Date.now();
    }

    // =========================================================
    // 2. DOWNLOAD INTI (ARIA2C & MEGATOOLS)
    // =========================================================
    _downloadFileWithAria2c(urlArray, outputFilename) {
        return new Promise(async (resolve, reject) => {
            console.log(`Memulai aria2c: ${outputFilename}`);
            await this._sendTelegramMessage(`⬇️ Download dimulai: \`${outputFilename}\``);

            const args = [
                '--allow-overwrite=true', '--file-allocation=none', '--summary-interval=0',
                '-x', '16', '-s', '16', '-c', '--async-dns=false',
                '-o', outputFilename, urlArray[0]
            ];

            const ariaProcess = spawn('aria2c', args);
            let totalSize = await this._getTotalFileSizeSafe(urlArray[0]);
            let lastNotifiedPercent = 0;

            const progressInterval = setInterval(() => {
                if (fs.existsSync(outputFilename) && totalSize) {
                    const currentSize = fs.statSync(outputFilename).size;
                    const percentNow = Math.floor((currentSize / totalSize) * 100);

                    if ((percentNow >= 50 && lastNotifiedPercent < 50) || percentNow >= 100) {
                        this._editTelegramMessage(`⬇️ Download \`${outputFilename}\` — ${percentNow}% (${this._humanReadableSize(currentSize)}/${this._humanReadableSize(totalSize)})`);
                        lastNotifiedPercent = percentNow;
                    }
                }
            }, 3000);

            ariaProcess.on('close', (code) => {
                clearInterval(progressInterval);
                if (code === 0 && fs.existsSync(outputFilename)) {
                    const finalSize = fs.statSync(outputFilename).size;
                    this._editTelegramMessage(`✅ Download Selesai. \`${outputFilename}\` (${this._humanReadableSize(finalSize)})`);
                    resolve(outputFilename);
                } else {
                    reject(new Error(`Aria2c exit code: ${code}`));
                }
            });
            
            ariaProcess.on('error', (err) => {
                clearInterval(progressInterval);
                reject(err);
            });
        });
    }

    _downloadFileWithMegatools(megaUrl) {
        return new Promise(async (resolve, reject) => {
            console.log(`Mengunduh dari MEGA: ${megaUrl}`);
            await this._sendTelegramMessage("⬇️ **Mulai mengunduh...**\n`megatools` sedang mengunduh file.");
            
            const originalCwd = process.cwd();
            const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mega-'));
            
            const megaProcess = spawn('megatools', ['dl', megaUrl], { cwd: tempDir });
            let lastNotifiedPercent = 0;

            megaProcess.stdout.on('data', (data) => {
                const output = data.toString();
                const match = output.match(/(\d+\.\d+)%\s+of\s+.*\((\d+\.\d+)\s*(\wB)\)/);
                if (match) {
                    const percentNow = Math.floor(parseFloat(match[1]));
                    if ((percentNow >= 50 && lastNotifiedPercent < 50) || percentNow === 100) {
                        lastNotifiedPercent = percentNow;
                        this._editTelegramMessage(`⬇️ **MEGA Downloading...**\nUkuran: \`${match[2]} ${match[3]}\`\nProgres: \`${percentNow}%\``);
                    }
                }
            });

            megaProcess.on('close', (code) => {
                if (code === 0) {
                    const files = fs.readdirSync(tempDir).filter(f => !f.endsWith('.megatools'));
                    if (files.length > 0) {
                        const filename = files[0];
                        fs.moveSync(path.join(tempDir, filename), path.join(originalCwd, filename), { overwrite: true });
                        this._editTelegramMessage(`✅ **MEGA: Unduhan selesai!**\nFile: \`${filename}\`\n\n**➡️ Mulai UPLOADING...**`);
                        resolve(filename);
                    } else {
                        reject(new Error("File MEGA tidak ditemukan setelah download."));
                    }
                } else {
                    reject(new Error(`Megatools exit code: ${code}`));
                }
                try { fs.removeSync(tempDir); } catch(e){}
            });
        });
    }

    // =========================================================
    // 3. PLAYWRIGHT (Pengganti Selenium)
    // =========================================================
    async _initializeBrowser() {
        try {
            this.browser = await chromium.launch({
                headless: false, // Karena pakai xvfb
                args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            });
            
            this.context = await this.browser.newContext({
                acceptDownloads: true,
                userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            });

            return true;
        } catch (e) {
            console.error("Gagal init Playwright:", e);
            return false;
        }
    }

    async _processPlaywrightDownload() {
        const page = await this.context.newPage();
        //await stealth().onPageCreated(page); // Terapkan Stealth
        await page.goto(this.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
        await this._editTelegramMessage(`⬇️ **[Mode Download]** Menganalisis situs...`);

        let downloadedFilename = null;

        try {
            if (this.url.includes("mediafire")) {
                await this._editTelegramMessage("⬇️ **[MediaFire Mode]** Ekstrak URL...");
                // Bypass form 1 jika ada, atau langsung cari tombol download
                const dlButton = page.locator('#downloadButton');
                await dlButton.waitFor({ state: 'visible', timeout: 30000 });
                
                const finalUrl = await dlButton.getAttribute('href');
                const fileName = await this._extractFilename(finalUrl);
                
                downloadedFilename = await this._downloadFileWithAria2c([finalUrl], fileName);

            } else if (this.url.includes("gofile")) {
                await this._editTelegramMessage("⬇️ **[Gofile Mode]** Mengklik tombol download...");
                
                // Monitor request untuk cari direct link atau handle download event
                const downloadPromise = page.waitForEvent('download', { timeout: 120000 });
                
                // Klik paksa karena Gofile sering ubah selector
                await page.evaluate(() => {
                    const btns = Array.from(document.querySelectorAll('button, a'));
                    const dlBtn = btns.find(b => b.textContent.toLowerCase().includes('download') || b.id.includes('download'));
                    if(dlBtn) dlBtn.click();
                });

                const download = await downloadPromise;
                downloadedFilename = download.suggestedFilename();
                const savePath = path.join(process.cwd(), downloadedFilename);
                await download.saveAs(savePath);
                
                this._editTelegramMessage(`✅ **Unduhan selesai!**\nFile: \`${downloadedFilename}\`\n\n**➡️ Mulai UPLOADING...**`);

            } else {
                // AGGRESSIVE FALLBACK
                await this._editTelegramMessage("⬇️ **[Mode Agresif]** Mencari tombol download...");
                const downloadPromise = page.waitForEvent('download', { timeout: 120000 });
                
                await page.evaluate(() => {
                    const btns = Array.from(document.querySelectorAll('button, a, input[type="submit"]'));
                    const dlBtn = btns.find(b => {
                        const text = (b.innerText || b.value || '').toLowerCase();
                        return text.includes('download') || text.includes('get');
                    });
                    if(dlBtn) dlBtn.click();
                });

                const download = await downloadPromise;
                downloadedFilename = download.suggestedFilename();
                await download.saveAs(path.join(process.cwd(), downloadedFilename));
                this._editTelegramMessage(`✅ **Agresif: Unduhan selesai!**\nFile: \`${downloadedFilename}\``);
            }

            return downloadedFilename;

        } catch (e) {
            throw new Error(`Playwright Error: ${e.message}`);
        }
    }

    // =========================================================
    // 4. MAIN ORCHESTRATOR
    // =========================================================
    async run() {
        await this._sendTelegramMessage(`⏳ **Menganalisis URL...**\nURL: \`${this.url}\``);
        let finalFilename = null;

        try {
            if (this.url.includes("mega.nz")) {
                finalFilename = await this._downloadFileWithMegatools(this.url);
            } 
            else if (this.url.includes("pixeldrain")) {
                const match = this.url.match(/pixeldrain\.com\/(u|l|f)\/([a-zA-Z0-9]+)/);
                if (!match) throw new Error("URL Pixeldrain tidak valid.");
                const fileId = match[2];
                
                const infoRes = await axios.get(`https://pixeldrain.com/api/file/${fileId}/info`);
                const filename = infoRes.data.name || `pixeldrain_${fileId}`;
                const dlUrl = `https://pixeldrain.com/api/file/${fileId}?download`;
                
                finalFilename = await this._downloadFileWithAria2c([dlUrl], filename);
            }
            else {
                const isBrowserInit = await this._initializeBrowser();
                if (!isBrowserInit) throw new Error("Gagal init Playwright.");
                
                // Untuk sourceforge & apkadmin bisa dihandle mirip mediafire (ekstrak link -> aria2c)
                // Sementara fallback ke Playwright download event
                finalFilename = await this._processPlaywrightDownload();
            }

            if (finalFilename) {
                // Tulis nama file agar bisa dibaca YAML step berikutnya
                fs.writeFileSync('downloaded_filename.txt', finalFilename);
                console.log(`[OK] File berhasil diunduh: ${finalFilename}`);
            }

        } catch (e) {
            console.error("Run Error:", e);
            await this._editTelegramMessage(`❌ **Unduhan GAGAL!**\nDetail: ${e.message.substring(0, 150)}`);
        } finally {
            await this.cleanup();
        }
    }
}

// =========================================================
// EKSEKUSI
// =========================================================
const urlToDownload = process.env.PAYLOAD_URL || process.argv[2];
if (!urlToDownload) {
    console.error("Tidak ada URL yang diberikan!");
    process.exit(1);
}

const bot = new DownloaderBot(urlToDownload);
bot.run();
