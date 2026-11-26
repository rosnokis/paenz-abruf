// Pänz-Sammler: liest freigegebene WhatsApp-Gruppen über ein verknüpftes Gerät mit,
// sammelt Flyer (Bilder, PDFs) und Nachrichten mit Datum oder Link und schickt einmal
// am Tag eine Sammelmail. Schreibt nie in Gruppen. Absender und Nummern landen nicht in der Mail.
import fs from 'node:fs';
import path from 'node:path';
import makeWASocket, { useMultiFileAuthState, downloadMediaMessage, DisconnectReason, Browsers } from '@whiskeysockets/baileys';
import nodemailer from 'nodemailer';
import QRCode from 'qrcode';
import pino from 'pino';

const E = process.env;
const DATEN = E.DATEN || '/data';
const GRUPPEN = (E.GRUPPEN || '').split(';').map(s => s.trim()).filter(Boolean);
const SENDEZEIT = E.SENDEZEIT || '05:30';
const MAIL_AN = E.MAIL_AN, SMTP_USER = E.SMTP_USER, SMTP_PASS = (E.SMTP_PASS || '').replace(/\s/g, '');
const MAX_MB = Number(E.MAX_MB || 18);
const AUFBEWAHREN_TAGE = Number(E.AUFBEWAHREN_TAGE || 30);
const EINGANG = path.join(DATEN, 'eingang'), LISTE = path.join(DATEN, 'eingang.jsonl'), STATUS = path.join(DATEN, 'status.json');
fs.mkdirSync(EINGANG, { recursive: true });
const log = (...a) => console.log(new Date().toLocaleString('de-DE', { timeZone: 'Europe/Berlin' }), ...a);

// ---------- Zustand ----------
const lies = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const status = Object.assign({ gesendetAm: null, abgemeldetGemeldet: false }, lies(STATUS, {}));
const sichern = () => fs.writeFileSync(STATUS, JSON.stringify(status, null, 1));
const eintraege = () => fs.existsSync(LISTE) ? fs.readFileSync(LISTE, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
const gesehen = new Set(eintraege().map(e => e.id));
const heute = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
const uhr = d => d.toLocaleTimeString('de-DE', { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit' });

// ---------- Was wird gesammelt ----------
export const DATUM = /\b\d{1,2}\.\s?\d{1,2}\.|\b\d{1,2}\.\s?(jan|feb|mär|maer|apr|mai|jun|jul|aug|sep|okt|nov|dez)|\b(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b|\b\d{1,2}(:\d\d)?\s?uhr\b/i;
export const LINK = /https?:\/\/\S+/i;
export function auswerten(m) {
  const c = m.message || {};
  const inner = c.ephemeralMessage?.message || c.viewOnceMessage?.message || c.documentWithCaptionMessage?.message || c;
  if (inner.imageMessage) return { art: 'bild', text: inner.imageMessage.caption || '', endung: 'jpg' };
  const doc = inner.documentMessage;
  if (doc && /pdf|image\//.test(doc.mimetype || '')) return { art: 'datei', text: doc.caption || doc.fileName || '', endung: doc.mimetype.includes('pdf') ? 'pdf' : (doc.mimetype.split('/')[1] || 'bin').replace('jpeg', 'jpg') };
  const text = inner.conversation || inner.extendedTextMessage?.text || '';
  if (text && (DATUM.test(text) || LINK.test(text))) return { art: 'text', text };
  return null;
}

// ---------- Mail ----------
const mailer = () => nodemailer.createTransport({ host: 'smtp.gmail.com', port: 465, secure: true, auth: { user: SMTP_USER, pass: SMTP_PASS } });
async function mail(betreff, text, anhaenge = []) {
  await mailer().sendMail({ from: `Pänz-Sammler <${SMTP_USER}>`, to: MAIL_AN, subject: betreff, text, attachments: anhaenge });
  log('Mail gesendet:', betreff);
}
export function teile(liste, maxBytes) {
  const out = []; let cur = [], size = 0;
  for (const e of liste) {
    const s = e.datei ? (fs.existsSync(e.datei) ? fs.statSync(e.datei).size : 0) : 0;
    if (cur.length && size + s > maxBytes) { out.push(cur); cur = []; size = 0; }
    cur.push(e); size += s;
  }
  if (cur.length) out.push(cur);
  return out;
}
async function sammelmail() {
  const alle = eintraege(), offen = alle.filter(e => !e.gesendet);
  const tag = new Date().toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit' });
  if (!offen.length) {
    await mail(`Pänz-Tipp Sammlung ${tag} (0)`, 'Keine neuen Flyer oder Termine in den Gruppen.\n\nGruppen: ' + GRUPPEN.join(', '));
  } else {
    const parts = teile(offen, MAX_MB * 1024 * 1024);
    for (const [i, part] of parts.entries()) {
      const zeilen = part.map((e, j) => `${j + 1}. [${e.gruppe}] ${e.tag.slice(8, 10)}.${e.tag.slice(5, 7)}. ${e.uhr}` + (e.datei ? ` – Anhang ${path.basename(e.datei)}` : '') + (e.text ? `\n   ${e.text.replace(/\n+/g, '\n   ')}` : ''));
      const teil = parts.length > 1 ? ` Teil ${i + 1}/${parts.length}` : '';
      await mail(`Pänz-Tipp Sammlung ${tag} (${part.length})${teil}`,
        'Gesammelt aus den WhatsApp-Gruppen (nur öffentliche Termine aufnehmen, Angaben beim Veranstalter prüfen):\n\n' + zeilen.join('\n\n'),
        part.filter(e => e.datei && fs.existsSync(e.datei)).map(e => ({ filename: path.basename(e.datei), path: e.datei })));
    }
  }
  const ids = new Set(offen.map(e => e.id));
  const grenze = Date.now() - AUFBEWAHREN_TAGE * 864e5;
  const bleiben = alle.map(e => ids.has(e.id) ? { ...e, gesendet: true } : e).filter(e => !e.gesendet || e.ts > grenze);
  fs.writeFileSync(LISTE, bleiben.map(e => JSON.stringify(e)).join('\n') + (bleiben.length ? '\n' : ''));
  for (const d of fs.readdirSync(EINGANG)) if (d < new Date(grenze).toISOString().slice(0, 10)) fs.rmSync(path.join(EINGANG, d), { recursive: true, force: true });
  status.gesendetAm = heute(); sichern();
}
function zeitplan() {
  setInterval(async () => {
    const jetzt = new Date().toLocaleTimeString('de-DE', { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit' });
    if (jetzt >= SENDEZEIT && status.gesendetAm !== heute()) {
      try { await sammelmail(); } catch (e) { log('Mail fehlgeschlagen:', e.message); }
    }
  }, 60_000);
}

// ---------- WhatsApp ----------
let gruppenJids = new Map();
async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(path.join(DATEN, 'anmeldung'));
  const sock = makeWASocket({ auth: state, logger: pino({ level: 'warn' }), browser: Browsers.ubuntu('Pänz-Sammler'), markOnlineOnConnect: false, syncFullHistory: false });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', async u => {
    if (u.qr) {
      await QRCode.toFile(path.join(DATEN, 'qr.png'), u.qr, { width: 400 });
      log('Neuer QR-Code in qr.png – mit dem Zweithandy scannen (WhatsApp → Verknüpfte Geräte → Gerät hinzufügen).');
      console.log(await QRCode.toString(u.qr, { type: 'terminal', small: true }));
    }
    if (u.connection === 'open') {
      log('Verbunden.'); status.abgemeldetGemeldet = false; sichern();
      try { fs.rmSync(path.join(DATEN, 'qr.png'), { force: true }); } catch {}
      const alle = await sock.groupFetchAllParticipating();
      const namen = Object.values(alle).map(g => [g.subject, g.id]);
      log('Gruppen des Kontos:', namen.map(n => n[0]).join(' | ') || '(keine)');
      gruppenJids = new Map(namen.filter(([n]) => GRUPPEN.includes(n)).map(([n, id]) => [id, n]));
      const fehlt = GRUPPEN.filter(n => ![...gruppenJids.values()].includes(n));
      log('Ausgewertet:', [...gruppenJids.values()].join(' | ') || '(keine)', fehlt.length ? '– nicht gefunden: ' + fehlt.join(' | ') : '');
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        log('Von WhatsApp abgemeldet. Ordner data/anmeldung löschen und Container neu starten, dann neu koppeln.');
        if (!status.abgemeldetGemeldet) {
          try { await mail('Pänz-Sammler: neu koppeln nötig', 'Der Sammler wurde von WhatsApp abgemeldet. Bitte in Container Manager den Ordner data/anmeldung löschen, den Container neu starten und den QR-Code (data/qr.png) mit dem Zweithandy scannen.'); } catch (e) { log(e.message); }
          status.abgemeldetGemeldet = true; sichern();
        }
      } else { log('Verbindung getrennt (' + code + '), neuer Versuch in 15 s.'); setTimeout(start, 15_000); }
    }
  });
  sock.ev.on('groups.update', ups => { for (const g of ups) if (g.subject && GRUPPEN.includes(g.subject)) gruppenJids.set(g.id, g.subject); });
  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const m of messages) {
      const jid = m.key?.remoteJid, id = m.key?.id;
      if (!jid || !gruppenJids.has(jid) || m.key.fromMe || gesehen.has(id)) continue;
      const a = auswerten(m); if (!a) continue;
      gesehen.add(id);
      const ts = Number(m.messageTimestamp || Date.now() / 1000) * 1000, d = new Date(ts);
      const tag = d.toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
      const e = { id, gruppe: gruppenJids.get(jid), ts, tag, uhr: uhr(d), art: a.art, text: a.text.slice(0, 1500) };
      if (a.art !== 'text') {
        try {
          const buf = await downloadMediaMessage(m, 'buffer', {}, { logger: pino({ level: 'warn' }), reuploadRequest: sock.updateMediaMessage });
          fs.mkdirSync(path.join(EINGANG, tag), { recursive: true });
          e.datei = path.join(EINGANG, tag, `${uhr(d).replace(':', '')}-${id.slice(-6)}.${a.endung}`);
          fs.writeFileSync(e.datei, buf);
        } catch (err) { log('Bild nicht ladbar:', err.message); e.text = (e.text + ' [Bild nicht ladbar]').trim(); }
      }
      fs.appendFileSync(LISTE, JSON.stringify(e) + '\n');
      log('Gesammelt:', e.gruppe, e.art, e.datei ? path.basename(e.datei) : '');
    }
  });
}

if (process.argv[2] === '--testmail') {
  await mail('Pänz-Sammler: Testmail', 'Der Mailversand funktioniert.'); process.exit(0);
} else if (process.argv[2] === '--jetzt-senden') {
  await sammelmail(); process.exit(0);
} else if (!process.env.SAMMLER_KEIN_START) {
  for (const [k, v] of Object.entries({ GRUPPEN: GRUPPEN.length, MAIL_AN, SMTP_USER, SMTP_PASS })) if (!v) { log('Einstellung fehlt in einstellungen.env:', k); process.exit(1); }
  log('Pänz-Sammler startet. Gruppen:', GRUPPEN.join(' | '), '· Sammelmail um', SENDEZEIT);
  zeitplan(); start();
}
