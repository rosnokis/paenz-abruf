// node --test test.mjs  (startet keine WhatsApp-Verbindung)
process.env.SAMMLER_KEIN_START = '1';
process.env.DATEN = (await import('node:os')).tmpdir() + '/paenz-sammler-test';
const { auswerten, teile } = await import('./sammler.mjs');
import test from 'node:test';
import assert from 'node:assert';

test('Bild wird immer gesammelt', () => {
  assert.equal(auswerten({ message: { imageMessage: { caption: 'Laternenfest' } } }).art, 'bild');
});
test('Text nur mit Datum oder Link', () => {
  assert.equal(auswerten({ message: { conversation: 'Hat jemand eine Babytrage?' } }), null);
  assert.equal(auswerten({ message: { conversation: 'Flohmarkt am 6.12. ab 10 Uhr' } }).art, 'text');
  assert.equal(auswerten({ message: { extendedTextMessage: { text: 'Infos: https://example.org/fest' } } }).art, 'text');
});
test('PDF ja, Sticker nein', () => {
  assert.equal(auswerten({ message: { documentMessage: { mimetype: 'application/pdf', fileName: 'Programm.pdf' } } }).endung, 'pdf');
  assert.equal(auswerten({ message: { stickerMessage: {} } }), null);
});
test('Mails werden nach Größe geteilt', () => {
  assert.deepEqual(teile([{ id: 1 }, { id: 2 }], 10).length, 1);
});
