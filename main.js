const { chromium, devices } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
chromium.use(StealthPlugin());

const axios = require('axios');
const fs = require('fs-extra');
const path = require('path');
const { spawn } = require('child_process');

class DownloaderBot {

constructor(url){

    this.url = url
    this.botToken = process.env.BOT_TOKEN
    this.ownerId = process.env.OWNER_ID
    this.initialMessageId = null

    this.browser = null
    this.context = null

    this.downloadDir = path.resolve("./downloads")

    fs.ensureDirSync(this.downloadDir)
}

_humanSize(bytes){

    if(!bytes) return "0 B"

    const i = Math.floor(Math.log(bytes)/Math.log(1024))

    return (bytes/Math.pow(1024,i)).toFixed(2) + " " +
    ["B","KB","MB","GB","TB"][i]
}

async _sendTelegramMessage(text){

    if(!this.botToken || !this.ownerId) return

    const res = await axios.post(
        `https://api.telegram.org/bot${this.botToken}/sendMessage`,
        {
            chat_id:this.ownerId,
            text,
            parse_mode:"Markdown"
        }
    )

    this.initialMessageId = res.data.result.message_id
}

async _editTelegramMessage(text){

    if(!this.initialMessageId)
        return this._sendTelegramMessage(text)

    await axios.post(
        `https://api.telegram.org/bot${this.botToken}/editMessageText`,
        {
            chat_id:this.ownerId,
            message_id:this.initialMessageId,
            text,
            parse_mode:"Markdown"
        }
    ).catch(()=>{})
}

async _getDirectMirrorSF(url){

    try{

        const parts = url.split('/')

        const project = parts[4]
        const filename = parts[6]

        const api =
`https://sourceforge.net/settings/mirror_choices?projectname=${project}&filename=${filename}`

        const res = await axios.get(api)

        const mirrors = res.data.mirrors

        if(!mirrors || mirrors.length === 0)
            return url

        const best = mirrors[0]

        return `${best.url}/${project}/files/${filename}`

    }catch(e){

        console.log("mirror api gagal")

        return url
    }
}

async _downloadWithAria2(url){

return new Promise((resolve,reject)=>{

    const aria = spawn("aria2c",[

        "-x16",
        "-s16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--summary-interval=3",
        "-d",this.downloadDir,
        url

    ])

    aria.stdout.on("data",async data=>{

        const text = data.toString()

        const match = text.match(/\((.*)%\).*DL:(.*)\]/)

        if(match){

            await this._editTelegramMessage(
`⬇️ **Aria2 Download**

📊 Progress: \`${match[1]}%\`
⚡ Speed: \`${match[2]}\``
            )
        }

    })

    aria.on("close",code=>{

        if(code===0){

            const files = fs.readdirSync(this.downloadDir)

            const latest = files
            .map(f=>({
                name:f,
                t:fs.statSync(path.join(this.downloadDir,f)).mtime
            }))
            .sort((a,b)=>b.t-a.t)[0]

            resolve(path.join(this.downloadDir,latest.name))

        }else{

            reject(new Error("aria2 failed"))

        }

    })

})
}

async _downloadWithCurl(targetUrl){

return new Promise(async (resolve,reject)=>{

    const urlParts = targetUrl.split('/')
    const fileName =
    urlParts[urlParts.length-2] || "downloaded_file.iso"

    const filePath = path.join(this.downloadDir,fileName)

    let totalSize = 0

    try{

        const head = await axios.head(targetUrl,{maxRedirects:5})

        totalSize =
        parseInt(head.headers["content-length"] || "0")

    }catch{}

    const curl = spawn("curl",[
        "-L",
        "-o",filePath,
        targetUrl
    ])

    let lastSize = 0
    let lastTime = Date.now()

    const interval = setInterval(async ()=>{

        if(!fs.existsSync(filePath)) return

        const stat = fs.statSync(filePath)

        const downloaded = stat.size

        const now = Date.now()

        const diff = (now-lastTime)/1000

        const speed = (downloaded-lastSize)/diff

        lastSize = downloaded
        lastTime = now

        const percent =
        totalSize ?
        (downloaded/totalSize*100).toFixed(1) : "?"

        let eta="?"

        if(totalSize && speed>0){

            const remain = totalSize-downloaded

            const sec = Math.floor(remain/speed)

            const m = Math.floor(sec/60)

            const s = sec%60

            eta = `${m}m ${s}s`
        }

        const speedMB = (speed/1024/1024).toFixed(2)

        await this._editTelegramMessage(
`⬇️ **Curl Download**

📄 File: \`${fileName}\`
📊 Progress: \`${percent}%\`
⚡ Speed: \`${speedMB} MB/s\`
📦 Downloaded: \`${this._humanSize(downloaded)} / ${this._humanSize(totalSize)}\`
⏳ ETA: \`${eta}\``
        )

    },5000)

    curl.on("close",code=>{

        clearInterval(interval)

        if(code===0){

            fs.writeFileSync(
                "downloaded_filename.txt",
                fileName
            )

            resolve(filePath)

        }else{

            reject(new Error("curl failed"))

        }

    })

})
}

async run(){

    if(this.url.includes("sourceforge.net")){

        await this._sendTelegramMessage(
            "🔎 mencari mirror tercepat..."
        )

        const direct =
        await this._getDirectMirrorSF(this.url)

        const finalFile =
        await this._downloadWithAria2(direct)

        return this._finish(finalFile)
    }

    await this._sendTelegramMessage(
        "⬇️ memulai download..."
    )

    const finalFile =
    await this._downloadWithCurl(this.url)

    this._finish(finalFile)
}

_finish(file){

    if(!file) return

    const size = fs.statSync(file).size

    const name = path.basename(file)

    fs.writeFileSync("downloaded_filename.txt",name)

    this._editTelegramMessage(
`✅ **Download Selesai**

📄 File: \`${name}\`
⚖️ Size: \`${this._humanSize(size)}\``
    )
}

}

const target =
process.env.PAYLOAD_URL || process.argv[2]

if(target)
new DownloaderBot(target).run()
