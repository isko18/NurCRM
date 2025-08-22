import pkg from '@whiskeysockets/baileys';
const {
  default: makeWASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
  jidNormalizedUser
} = pkg;

import QRCode from 'qrcode';
import fetch from 'node-fetch';
import path from 'path';
import fs from 'fs';

// ⚡ Аргументы: companyId [action] [params...]
const [,, companyId, action, ...params] = process.argv;
if (!companyId) {
  console.error("❌ Укажите companyId!");
  process.exit(1);
}

const API_URL = "http://127.0.0.1:8000/api/wa/webhook"; 

async function sendToDjango(endpoint, payload) {
  try {
    await fetch(`${API_URL}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error("Ошибка отправки в Django:", err.message);
  }
}

async function initSock() {
  const authDir = path.join("auth", companyId);
  if (!fs.existsSync(authDir)) fs.mkdirSync(authDir, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    browser: ["MiniCRM", "Chrome", "1.0.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  return sock;
}

async function startSession() {
  const sock = await initSock();

  sock.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      const qrDataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 256 });
      await sendToDjango("/qr/", { company_id: companyId, qr: qrDataUrl });
      console.log("📲 QR обновлен");
    }

    if (connection === "open") {
      console.log("✅ WA подключен");
      await sendToDjango("/status/", { company_id: companyId, status: "open" });
    }

    if (connection === "close") {
      const reason = lastDisconnect?.error?.output?.statusCode;
      console.log("⚠️ WA отключен", reason);
      await sendToDjango("/status/", { company_id: companyId, status: "close", reason });
    }
  });

  sock.ev.on("messages.upsert", async ({ messages }) => {
    for (const msg of messages) {
      if (!msg.message) continue;

      const from = msg.key?.remoteJid;
      if (!from || from.endsWith("@g.us") || from === "status@broadcast") continue;

      const userJid = jidNormalizedUser(from);
      const phone = userJid.replace("@s.whatsapp.net", "");
      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        msg.message?.imageMessage?.caption ||
        msg.message?.videoMessage?.caption ||
        "";

      let type = "text";
      if (msg.message?.imageMessage) type = "image";
      if (msg.message?.videoMessage) type = "video";
      if (msg.message?.documentMessage) type = "document";
      if (msg.message?.audioMessage) type = "audio";

      await sendToDjango("/message/", {
        company_id: companyId,
        phone,
        text,
        type,
        direction: "in",
      });
    }
  });
}

async function sendText(phone, text) {
  const sock = await initSock();
  const jid = phone.includes("@s.whatsapp.net") ? phone : `${phone}@s.whatsapp.net`;
  await sock.sendMessage(jid, { text });

  await sendToDjango("/message/", {
    company_id: companyId,
    phone,
    text,
    direction: "out",
    type: "text",
  });
}

async function sendMedia(phone, type, url, caption) {
  const sock = await initSock();
  const jid = phone.includes("@s.whatsapp.net") ? phone : `${phone}@s.whatsapp.net`;

  const resp = await fetch(url);
  const buffer = Buffer.from(await resp.arrayBuffer());

  let content = {};
  if (type === "image") content = { image: buffer, caption };
  if (type === "video") content = { video: buffer, caption, mimetype: "video/mp4" };
  if (type === "audio") content = { audio: buffer, mimetype: "audio/mpeg" };
  if (type === "document") content = { document: buffer, fileName: "file", mimetype: "application/pdf" };

  await sock.sendMessage(jid, content);

  await sendToDjango("/message/", {
    company_id: companyId,
    phone,
    caption,
    direction: "out",
    type,
  });
}

// 🔥 Обработка команд
(async () => {
  if (!action) {
    await startSession();
  } else if (action === "sendText") {
    const [phone, text] = params;
    await sendText(phone, text);
  } else if (action === "sendMedia") {
    const [phone, type, url, caption] = params;
    await sendMedia(phone, type, url, caption || "");
  }
})();
