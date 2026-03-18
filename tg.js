const { TelegramClient, Api } = require("telegram");
const { StringSession } = require("telegram/sessions");
const fs = require("fs-extra");
const path = require("path");
const axios = require("axios");

// =========================================================
// KONFIGURASI
// =========================================================
const BOT_TOKEN = process.env.BOT_TOKEN;
const API_ID = parseInt(process.env.API_ID);
const API_HASH = process.env.API_HASH;
const OWNER_ID = process.env.OWNER_ID; // Chat ID tujuan
const FILENAME_MARKER = "downloaded_filename.txt";

// Sesi string kosong untuk login bot baru setiap kali jalan
const stringSession = new StringSession("");

// =========================================================
// FUNGSI NOTIFIKASI (Utils)
// =========================================================

async function sendTelegramMessage(text) {
    if (!BOT_TOKEN || !OWNER_ID) return null;
    try {
        const res = await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
            chat_id: OWNER_ID,
            text: text,
            parse_mode: "Markdown"
        });
        return res.data.result.message_id;
    } catch (e) {
        console.error("❌ Gagal kirim pesan:", e.message);
        return null;
    }
}

async function editTelegramMessage(messageId, text) {
    if (!messageId) return;
    try {
        await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/editMessageText`, {
            chat_id: OWNER_ID,
            message_id: messageId,
            text: text,
            parse_mode: "Markdown"
        });
    } catch (e) {
        // Abaikan error edit jika tidak krusial
    }
}

// =========================================================
// FUNGSI UPLOAD UTAMA
// =========================================================

async function uploadLargeFile(filePath) {
    if (!API_ID || !API_HASH || !BOT_TOKEN || !OWNER_ID) {
        console.error("❌ Konfigurasi Environment tidak lengkap!");
        return false;
    }

    const fileName = path.basename(filePath);
    const stats = fs.statSync(filePath);
    const fileSizeGB = (stats.size / (1024 ** 3)).toFixed(2);

    const client = new TelegramClient(stringSession, API_ID, API_HASH, {
        connectionRetries: 5,
    });

    try {
        // Login sebagai Bot
        await client.start({
            botAuthToken: BOT_TOKEN,
        });

        console.log(`🚀 Memulai upload: ${fileName} (${fileSizeGB} GB)`);
        const messageId = await sendTelegramMessage(`⬆️ **Memulai Unggah (GramJS)...**\nFile: \`${fileName}\`\nUkuran: ${fileSizeGB} GB`);

        let lastNotifiedPercent = 0;

        // Proses Upload
        await client.sendFile(OWNER_ID, {
            file: filePath,
            caption: `✅ **${fileName}** selesai diunggah!`,
            workers: 4, // Meningkatkan kecepatan upload
            progressCallback: (progress) => {
                const percent = Math.round(progress * 100);
                
                // Logika 2X Update: 50% dan 100%
                const shouldUpdate50 = (percent >= 50 && lastNotifiedPercent < 50);
                const shouldUpdate100 = (percent >= 100 && lastNotifiedPercent < 100);

                if (shouldUpdate50 || shouldUpdate100) {
                    lastNotifiedPercent = percent;
                    const progressText = `⬆️ **Mengunggah (GramJS)...**\nFile: \`${fileName}\`\nProgres: \`${percent}%\``;
                    editTelegramMessage(messageId, progressText);
                    console.log(`Upload Progress: ${percent}%`);
                }
            },
        });

        await editTelegramMessage(messageId, `🎉 **Unggahan Selesai!**\nFile: \`${fileName}\``);
        await client.disconnect();
        return true;

    } catch (err) {
        let errorMsg = `❌ **Unggahan GAGAL:** ${err.message}`;
        if (err.message.includes("FLOOD")) {
            errorMsg = `❌ **FloodWait:** Telegram membatasi upload sementara.`;
        }
        console.error(errorMsg);
        await sendTelegramMessage(errorMsg);
        await client.disconnect();
        return false;
    }
}

// =========================================================
// EKSEKUSI
// =========================================================

(async () => {
    console.log("Checking file status...");

    if (!fs.existsSync(FILENAME_MARKER)) {
        console.error("❌ Marker file tidak ditemukan.");
        process.exit(1);
    }

    const actualFilename = fs.readFileSync(FILENAME_MARKER, "utf8").trim();

    if (!actualFilename || !fs.existsSync(actualFilename)) {
        console.error(`❌ File ${actualFilename} tidak ditemukan.`);
        process.exit(1);
    }

    const success = await uploadLargeFile(actualFilename);

    if (success) {
        try {
            fs.removeSync(actualFilename);
            fs.removeSync(FILENAME_MARKER);
            console.log("✅ Cleanup selesai.");
        } catch (e) {
            console.error("⚠️ Gagal menghapus file:", e.message);
        }
    } else {
        process.exit(1);
    }
})();
