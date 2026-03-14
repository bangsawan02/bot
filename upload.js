const { google } = require('googleapis');
const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const crypto = require('crypto');
const mime = require('mime-types');

// =========================================================
// KONFIGURASI
// =========================================================
const BOT_TOKEN = process.env.BOT_TOKEN;
const OWNER_ID = process.env.OWNER_ID || process.env.PAYLOAD_SENDER;
const REFRESH_TOKEN = process.env.DRIVE_REFRESH_TOKEN;
const CLIENT_ID = process.env.GOOGLE_CLIENT_ID;
const CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET;
const DRIVE_UPLOAD_FOLDER_NAME = "my-drive-upload";

let initialMessageId = null;

// =========================================================
// HELPERS
// =========================================================

async function sendTelegram(text) {
    if (!BOT_TOKEN || !OWNER_ID) return null;
    try {
        const res = await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
            chat_id: OWNER_ID, text, parse_mode: "Markdown"
        });
        return res.data.result.message_id;
    } catch (e) { console.error("TG Error:", e.message); return null; }
}

async function editTelegram(messageId, text) {
    if (!messageId) return;
    try {
        await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/editMessageText`, {
            chat_id: OWNER_ID, message_id: messageId, text, parse_mode: "Markdown"
        });
    } catch (e) {}
}

function humanSize(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(2) + " " + ["B", "KB", "MB", "GB"][i];
}

function calculateMD5(filePath) {
    return new Promise((resolve, reject) => {
        const hash = crypto.createHash('md5');
        const stream = fs.createReadStream(filePath);
        stream.on('data', data => hash.update(data));
        stream.on('end', () => resolve(hash.digest('hex')));
        stream.on('error', reject);
    });
}

// =========================================================
// DRIVE LOGIC
// =========================================================

async function getDriveService() {
    const oauth2Client = new google.auth.OAuth2(CLIENT_ID, CLIENT_SECRET);
    oauth2Client.setCredentials({ refresh_token: REFRESH_TOKEN });
    return google.drive({ version: 'v3', auth: oauth2Client });
}

async function getOrCreateFolder(drive, folderName) {
    const res = await drive.files.list({
        q: `name='${folderName}' and mimeType='application/vnd.google-apps.folder' and trashed=false`,
        fields: 'files(id)'
    });
    const files = res.data.files;
    if (files.length > 0) return files[0].id;

    const folder = await drive.files.create({
        resource: { name: folderName, mimeType: 'application/vnd.google-apps.folder' },
        fields: 'id'
    });
    return folder.data.id;
}

async function uploadFile(drive, filePath) {
    const folderId = await getOrCreateFolder(drive, DRIVE_UPLOAD_FOLDER_NAME);
    const fileName = path.basename(filePath);
    const fileSize = fs.statSync(filePath).size;
    const mimeType = mime.lookup(filePath) || 'application/octet-stream';

    const localMD5 = await calculateMD5(filePath);
    
    initialMessageId = await sendTelegram(`🚀 Mulai upload \`${fileName}\` ke Google Drive...`);

    let lastNotifiedPercent = 0;

    const res = await drive.files.create({
        requestBody: { name: fileName, parents: [folderId] },
        media: {
            mimeType: mimeType,
            body: fs.createReadStream(filePath)
        },
        fields: 'id, webViewLink, webContentLink, md5Checksum'
    }, {
        // Logika Resumable & Progress
        onUploadProgress: evt => {
            const progress = (evt.bytesRead / fileSize) * 100;
            const percent = Math.floor(progress);

            // LOGIKA 2X UPDATE: 50% dan 100%
            if ((percent >= 50 && lastNotifiedPercent < 50) || (percent === 100 && lastNotifiedPercent < 100)) {
                lastNotifiedPercent = percent;
                const text = `⏫ Uploading \`${fileName}\` — ${percent}% (${humanSize(evt.bytesRead)}/${humanSize(fileSize)})`;
                editTelegram(initialMessageId, text);
                console.log(`Upload Progress: ${percent}%`);
            }
        }
    });

    const driveFile = res.data;

    // Verifikasi MD5
    if (driveFile.md5Checksum.toLowerCase() === localMD5.toLowerCase()) {
        console.log("✅ Verifikasi MD5 Berhasil.");
        
        // Set Public
        await drive.permissions.create({
            fileId: driveFile.id,
            requestBody: { role: 'reader', type: 'anyone' }
        });

        const finalInfo = await drive.files.get({
            fileId: driveFile.id,
            fields: 'webViewLink, webContentLink'
        });

        const msg = `🎉 **UPLOAD SUKSES!**\n\n` +
                    `📄 File: \`${fileName}\`\n` +
                    `⚖️ Size: \`${humanSize(fileSize)}\`\n` +
                    `🔑 MD5: \`${localMD5}\`\n\n` +
                    `🌍 [Lihat di Drive](${finalInfo.data.webViewLink})\n` +
                    `⬇️ [Download Langsung](${finalInfo.data.webContentLink})`;
        
        await editTelegram(initialMessageId, msg);
        return true;
    } else {
        throw new Error("MD5 Mismatch! File korup saat upload.");
    }
}

// =========================================================
// RUN
// =========================================================

async function main() {
    try {
        if (!fs.existsSync('downloaded_filename.txt')) throw new Error("File 'downloaded_filename.txt' tidak ada.");
        const filePath = fs.readFileSync('downloaded_filename.txt', 'utf8').trim();
        
        if (!fs.existsSync(filePath)) throw new Error(`File ${filePath} tidak ditemukan di storage.`);

        const drive = await getDriveService();
        await uploadFile(drive, filePath);
        
    } catch (e) {
        console.error(e);
        await sendTelegram(`❌ **Upload GAGAL!**\nError: \`${e.message}\``);
        process.exit(1);
    }
}

main();
