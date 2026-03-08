const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const { spawn } = require('child_process');

class DownloaderBot {

constructor(url) {
    this.url = url;
    this.botToken = process.env.BOT_TOKEN;
    this.ownerId = process.env.OWNER_ID;
    this.initialMessageId = null;
    this.browser = null;
    this.context = null;
    this.downloadDir = path.resolve('./downloads');

    fs.ensureDirSync(this.downloadDir);

    this.selectors = this._loadSelectorsByDomain();
}

_loadSelectorsByDomain() {
    try {
        if (fs.existsSync('selectors.json')) {
            const data = fs.readJsonSync('selectors.json');
            const domain = new URL(this.url).hostname.replace('www.', '');
            return data[domain] || data['default'];
        }
    } catch (e) {
        console.log("selectors.json gagal dibaca, pakai default.");
    }

    return [
        "a[href*='download']",
        "a[download]",
        "button:has-text('Download')",
        "form"
    ];
}

_humanSize(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B","KB","MB","GB","TB"][i];
}

async _sendTelegramMessage(text) {
    if (!this.botToken || !this.ownerId) return;

    try {
        const res = await axios.post(`https://api.telegram.org/bot${this.botToken}/sendMessage`, {
            chat_id: this.ownerId,
            text,
            parse_mode: "Markdown"
        });

        this.initialMessageId = res.data.result.message_id;
    } catch (e) {
        console.error("Telegram error:", e.message);
    }
}

async _editTelegramMessage(text) {

    if (!this.initialMessageId)
        return this._sendTelegramMessage(text);

    try {
        await axios.post(`https://api.telegram.org/bot${this.botToken}/editMessageText`, {
            chat_id: this.ownerId,
            message_id: this.initialMessageId,
            text,
            parse_mode: "Markdown"
        }).catch(()=>{});
    } catch {}
}


async _downloadWithCurl(targetUrl) {

return new Promise((resolve, reject) => {

    const fileName = path.basename(new URL(targetUrl).pathname) || "downloaded_file";
    const filePath = path.join(this.downloadDir, fileName);

    const curlArgs = [
        "-L",
        "--progress-bar",
        "-o", filePath,
        targetUrl
    ];

    const curl = spawn("curl", curlArgs);

    let buffer = "";
    let lastUpdate = 0;

    curl.stderr.on("data", async (data) => {

        buffer += data.toString();

        const match = buffer.match(/(\d{1,3})%/);

        if (match) {

            const percent = match[1];
            const now = Date.now();

            if (now - lastUpdate > 6000) {

                lastUpdate = now;

                await this._editTelegramMessage(
`⬇️ **Curl Download**

📄 File: \`${fileName}\`
📊 Progress: \`${percent}%\``
                );

            }

            buffer = "";
        }

    });

    curl.on("close", (code) => {

        if (code === 0) {

            fs.writeFileSync("downloaded_filename.txt", fileName);

            resolve(filePath);

        } else {

            reject(new Error("Curl exit code " + code));

        }

    });

});
}

async _downloadWithAria2(url) {

    if (!url) return null;

    await this._editTelegramMessage(`🚀 **Aria2:** downloading...`);

    return new Promise((resolve, reject) => {

        const aria = spawn("aria2c", [
            "-x16",
            "-s16",
            "--summary-interval=3",
            "--file-allocation=none",
            "--auto-file-renaming=false",
            "-d", this.downloadDir,
            url
        ]);

        let lastUpdate = 0;
        let fileName = "detecting...";

        aria.stdout.on("data", async data => {

            const output = data.toString();

            const nameMatch = output.match(/Saving to: .*\/(.+)/) ||
                              output.match(/Saving to: (.+)/);

            if (nameMatch && fileName === "detecting...")
                fileName = nameMatch[1].trim();

            const progressMatch = output.match(/\((.*)%\).*DL:(.*)\]/);

            if (progressMatch) {

                const now = Date.now();

                if (now - lastUpdate > 6000) {

                    lastUpdate = now;

                    await this._editTelegramMessage(
`⬇️ **Aria2 Progress**

📄 File: \`${fileName}\`
📊 Progress: \`${progressMatch[1]}%\`
⚡ Speed: \`${progressMatch[2]}\``
                    );
                }
            }

        });

        aria.on("close", code => {

            if (code === 0) {

                const files = fs.readdirSync(this.downloadDir);

                if (!files.length)
                    return resolve(null);

                const sorted = files
                    .map(f => ({
                        name: f,
                        time: fs.statSync(path.join(this.downloadDir,f)).mtime
                    }))
                    .sort((a,b)=>b.time-a.time);

                resolve(path.join(this.downloadDir, sorted[0].name));

            } else {

                reject(new Error("Aria2 exit code " + code));

            }

        });

    });

}


async _processDefault() {

    let page = await this.context.newPage();

    await this.context.route("**/*", route => {

        const url = route.request().url();

        if ([
            "analytics",
            "adskeeper",
            "doubleclick",
            "googletagmanager",
            "google-analytics",
            "taboola",
            "outbrain",
            "popads"
        ].some(d => url.includes(d)))
            return route.abort();

        route.continue();

    });

    for (let attempt = 1; attempt <= 2; attempt++) {

        await this._editTelegramMessage(`🔎 scanning attempt ${attempt}/2`);

        const downloadPromise =
            this.context.waitForEvent("download",{timeout:45000}).catch(()=>null);

        try {

            await page.goto(this.url,{
                waitUntil:"domcontentloaded",
                timeout:60000
            });

            await page.waitForTimeout(3000);

            for (const selector of this.selectors) {

                try {

                    const el = page.locator(selector).first();

                    await el.waitFor({state:"attached",timeout:5000});

                    const tag = await el.evaluate(e=>e.tagName.toLowerCase());

                    if (tag==="form") {

                        await el.evaluate(f=>f.submit());

                    } else {

                        const href = await el.getAttribute("href");

                        if (href && href.startsWith("http") && !href.includes("javascript"))
                            return await this._downloadWithAria2(href);

                        await el.click({force:true});

                    }

                    break;

                } catch {}

            }

            const dlObj = await downloadPromise;

            if (dlObj) {

                const directUrl = dlObj.url();

                await dlObj.cancel();

                return await this._downloadWithAria2(directUrl);

            }

        } catch {
            console.log("attempt failed");
        }

    }

    throw new Error("Download link not found.");

}


async run() {

    if (this.url.includes("sourceforge.net")) {

        try {

            const finalFile = await this._downloadWithCurl(this.url);

            return this._finish(finalFile);

        } catch (e) {

            await this._editTelegramMessage(
                `❌ Curl failed: ${e.message}, fallback Playwright`
            );

        }

    }

    await this._sendTelegramMessage("⏳ **Bot started (Playwright)**");

    try {

        this.browser = await chromium.launch({
            headless:true,
            args:["--no-sandbox"]
        });

        this.context = await this.browser.newContext({
            ...devices["Desktop Chrome"],
            acceptDownloads:true
        });

        const finalFile = await this._processDefault();

        if (finalFile)
            this._finish(finalFile);

    } catch (e) {

        await this._editTelegramMessage(`❌ **Error:** ${e.message}`);

        process.exit(1);

    } finally {

        if (this.browser)
            await this.browser.close();

    }

}

_finish(filePath) {

    if (!filePath) return;

    const size = fs.statSync(filePath).size;
    const name = path.basename(filePath);

    fs.writeFileSync("downloaded_filename.txt", name);

    this._editTelegramMessage(
`✅ **Download Finished**

📄 File: \`${name}\`
⚖️ Size: \`${this._humanSize(size)}\``
    );

}

}


const target = process.env.PAYLOAD_URL || process.argv[2];

if (target)
    new DownloaderBot(target).run();
