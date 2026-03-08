const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const { spawn } = require('child_process');

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

            if (fs.existsSync('selectors.json')) {

                const data = fs.readJsonSync('selectors.json');
                const domain = new URL(this.url).hostname.replace('www.', '');

                return data[domain] || data['default'];

            }

        } catch (e) {}

        return [
            "a[href^='http']",
            "form",
            "button:has-text('Download')"
        ];
    }

    _humanSize(bytes) {

        if (!bytes || bytes === 0) return "0 B";

        const i = Math.floor(Math.log(bytes) / Math.log(1024));

        return (
            (bytes / Math.pow(1024, i)).toFixed(2)
            + " "
            + ["B", "KB", "MB", "GB"][i]
        );
    }

    async _sendTelegramMessage(text) {

        if (!this.botToken || !this.ownerId) return;

        try {

            const res = await axios.post(
                `https://api.telegram.org/bot${this.botToken}/sendMessage`,
                {
                    chat_id: this.ownerId,
                    text,
                    parse_mode: "Markdown"
                }
            );

            this.initialMessageId = res.data.result.message_id;

        } catch (e) {}
    }

    async _editTelegramMessage(text) {

        if (!this.initialMessageId) {
            return this._sendTelegramMessage(text);
        }

        try {

            await axios.post(
                `https://api.telegram.org/bot${this.botToken}/editMessageText`,
                {
                    chat_id: this.ownerId,
                    message_id: this.initialMessageId,
                    text,
                    parse_mode: "Markdown"
                }
            );

        } catch (e) {}

    }

    async _resolveSourceForge(url) {

        try {

            const res = await axios({
                url: url,
                method: "GET",
                maxRedirects: 0,
                validateStatus: null
            });

            if (
                res.status >= 300 &&
                res.status < 400 &&
                res.headers.location
            ) {

                let redirect = res.headers.location;

                if (redirect.startsWith("//")) {
                    redirect = "https:" + redirect;
                }

                return redirect;

            }

            const res2 = await axios.get(url, { maxRedirects: 10 });

            return res2.request.res.responseUrl;

        } catch (e) {

            throw new Error(
                "Resolve SourceForge gagal: " + e.message
            );

        }
    }

    async _downloadWithAria2(url) {

        if (!url) return null;

        await this._editTelegramMessage(
            "🚀 Aria2 mulai download..."
        );

        return new Promise((resolve, reject) => {

            const aria = spawn(
                'aria2c',
                [
                    '-x16',
                    '-s16',
                    '--summary-interval=3',
                    '--file-allocation=none',
                    '--auto-file-renaming=false',
                    url
                ]
            );

            let fileName = "Detecting...";
            let lastUpdate = 0;

            aria.stdout.on(
                'data',
                async (data) => {

                    const output = data.toString();

                    const nameMatch =
                        output.match(/Saving to: .*\/(.+)/);

                    if (nameMatch) {
                        fileName = nameMatch[1];
                    }

                    const progressMatch =
                        output.match(/\((.*)%\).*DL:(.*)\]/);

                    if (progressMatch) {

                        const now = Date.now();

                        if (now - lastUpdate > 4000) {

                            lastUpdate = now;

                            await this._editTelegramMessage(
`⬇️ Download

📄 ${fileName}
📊 ${progressMatch[1]}%
⚡ ${progressMatch[2]}`
                            );

                        }

                    }

                }
            );

            aria.on(
                'close',
                (code) => {

                    if (code !== 0) {
                        reject(new Error("aria2 gagal"));
                        return;
                    }

                    const files = fs.readdirSync('.')
                        .filter(f => !f.endsWith('.aria2'))
                        .map(f => ({
                            name: f,
                            time: fs.statSync(f).mtime
                        }))
                        .sort((a,b)=>b.time-a.time);

                    resolve(
                        files.length
                        ? files[0].name
                        : null
                    );

                }
            );

        });

    }

    async _handleSourceForge() {

        await this._editTelegramMessage(
            "🔎 Detect SourceForge mirror..."
        );

        const resolved =
            await this._resolveSourceForge(this.url);

        await this._editTelegramMessage(
            `✅ Mirror ditemukan\n\n${resolved}`
        );

        return this._downloadWithAria2(resolved);

    }

    async _processDefault() {

        const page = await this.context.newPage();

        await page.goto(
            this.url,
            {
                waitUntil: 'domcontentloaded',
                timeout: 60000
            }
        );

        await page.waitForTimeout(3000);

        for (const selector of this.selectors) {

            try {

                const el =
                    page.locator(selector).first();

                await el.waitFor({
                    state: 'attached',
                    timeout: 5000
                });

                const href =
                    await el.getAttribute('href');

                if (
                    href &&
                    href.startsWith("http")
                ) {

                    return this._downloadWithAria2(href);

                }

                await el.click({ force: true });

            } catch (e) {}

        }

        throw new Error(
            "Link download tidak ditemukan"
        );

    }

    async run() {

        await this._sendTelegramMessage(
            "⏳ Bot start"
        );

        try {

            if (
                this.url.includes(
                    "sourceforge.net"
                )
            ) {

                const file =
                    await this._handleSourceForge();

                const size =
                    fs.statSync(file).size;

                await this._editTelegramMessage(
`✅ Selesai

📄 ${file}
⚖️ ${this._humanSize(size)}`
                );

                return;
            }

            this.browser =
                await chromium.launch({
                    headless: false,
                    args: ['--no-sandbox']
                });

            this.context =
                await this.browser.newContext({
                    ...devices['iPhone 13'],
                    acceptDownloads: true
                });

            const file =
                await this._processDefault();

            const size =
                fs.statSync(file).size;

            await this._editTelegramMessage(
`✅ Selesai

📄 ${file}
⚖️ ${this._humanSize(size)}`
            );

        } catch (e) {

            await this._editTelegramMessage(
                `❌ Error\n${e.message}`
            );

        } finally {

            if (this.browser) {
                await this.browser.close();
            }

        }

    }

}

const target =
    process.env.PAYLOAD_URL
    || process.argv[2];

if (target) {
    new DownloaderBot(target).run();
}
