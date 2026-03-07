const axios = require('axios');
const { spawn } = require('child_process');
const fs = require('fs-extra');

// --- CONFIG ---
const BOT_TOKEN = process.env.BOT_TOKEN;
const OWNER_ID = process.env.OWNER_ID;
const URL_TARGET = process.env.PAYLOAD_URL;

async function notify(text) {
    console.log(text);
    if (!BOT_TOKEN || !OWNER_ID) return;
    await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
        chat_id: OWNER_ID, text: text, parse_mode: "Markdown"
    }).catch(() => {});
}

async function solveSourceForge() {
    try {
        await notify("📡 **Mencoba Taktik API Bypass (No Browser)...**");

        // 1. Ekstrak Project Name & Path dari URL
        // Contoh: https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS14/.../download
        const urlObj = new URL(URL_TARGET);
        const parts = urlObj.pathname.split('/');
        const projectName = parts[2];
        // Ambil path di antara '/files/' dan '/download'
        const fileIndex = parts.indexOf('files');
        const downloadIndex = parts.indexOf('download');
        const filePath = parts.slice(fileIndex + 1, downloadIndex).join('/');

        await notify(`📦 Project: \`${projectName}\`\n📁 Path: \`${filePath}\``);

        // 2. Tembak API SourceForge langsung (Seringkali tidak ada Cloudflare di sini)
        // Kita minta daftar mirror dalam format JSON
        const apiUrl = `https://sourceforge.net/settings/mirror_choices?projectname=${projectName}&filename=${filePath}&format=json`;
        
        const response = await axios.get(apiUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json'
            }
        });

        const mirrors = response.data.mirrors;
        if (!mirrors || mirrors.length === 0) throw new Error("API tidak mengembalikan daftar mirror.");

        // 3. Ambil 5 mirror terbaik
        const topMirrors = mirrors.slice(0, 5).map(m => {
            return `https://${m.shortname}.dl.sourceforge.net/project/${projectName}/${filePath}`;
        });

        await notify(`✅ Berhasil dapat ${topMirrors.length} mirror via API.`);

        // 4. Hajar pakai Aria2c
        const fileName = filePath.split('/').pop();
        await download(topMirrors, fileName);

    } catch (e) {
        await notify(`❌ **Taktik API Gagal:** ${e.message}\nSitus ini benar-benar memblokir IP GitHub.`);
    }
}

async function download(urls, fileName) {
    await notify(`⬇️ Memulai Aria2c untuk \`${fileName}\`...`);
    const args = ['-x16', '-s16', '-j16', '-k1M', '--file-allocation=none', '-o', fileName, ...urls];
    
    const aria = spawn('aria2c', args);
    aria.on('close', (code) => {
        if (code === 0) notify(`🎉 **FINISH!** File \`${fileName}\` siap di-upload.`);
        else notify(`💀 Aria2c gagal (Code: ${code})`);
    });
}

solveSourceForge();
