// node --test test.mjs  (startet keine WhatsApp-Verbindung)
process.env.SAMMLER_KEIN_START = '1';
process.env.DATEN = (await import('node:os')).tmpdir() + '/paenz-sammler-test';
const mod = await import('./sammler.mjs');
const { auswerten, teile } = mod;
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
test('Dateigröße aus Zahl und Long-Objekt', () => {
  const { groesse } = mod;
  assert.equal(auswerten({ message: { documentMessage: { mimetype: 'application/pdf', fileLength: 2048 } } }).bytes, 2048);
  assert.equal(groesse({ toNumber: () => 5e9 }), 5e9);
  assert.equal(groesse(undefined), 0);
});
test('Dateiendung enthält nur Buchstaben und Ziffern', () => {
  assert.equal(auswerten({ message: { documentMessage: { mimetype: 'image/../x' } } }).endung, 'bin');
  assert.equal(auswerten({ message: { documentMessage: { mimetype: 'image/jpeg' } } }).endung, 'jpg');
});
test('Gruppen: fest über die ID, fremde Gruppe gleichen Namens wird ignoriert', () => {
  const { gruppenZuordnen } = mod;
  // erster Start: genau eine Gruppe mit dem Namen -> wird gemerkt
  let r = gruppenZuordnen([['Kita Eltern', 'a@g.us'], ['Anderes', 'b@g.us']], ['Kita Eltern'], {});
  assert.deepEqual(r.neu, { 'a@g.us': 'Kita Eltern' });
  // später taucht eine zweite Gruppe mit gleichem Namen auf -> nur die gemerkte zählt
  r = gruppenZuordnen([['Kita Eltern', 'a@g.us'], ['Kita Eltern', 'fremd@g.us']], ['Kita Eltern'], { 'a@g.us': 'Kita Eltern' });
  assert.deepEqual(r.neu, { 'a@g.us': 'Kita Eltern' });
  // gemerkte Gruppe umbenannt/verlassen, fremde trägt den Namen -> keine Übernahme
  r = gruppenZuordnen([['Kita Eltern', 'fremd@g.us']], ['Kita Eltern'], { 'a@g.us': 'Kita Eltern' });
  assert.deepEqual(r.neu, {});
  // beim ersten Start zwei gleichnamige Gruppen -> keine wird ausgewertet
  r = gruppenZuordnen([['X', '1@g.us'], ['X', '2@g.us']], ['X'], {});
  assert.deepEqual(r.neu, {});
  assert.match(r.warnungen[0], /2 Gruppen/);
});
test('Gruppen: gemerkte Zuordnung bleibt, auch wenn die Gruppe zeitweise fehlt', () => {
  const { gruppenZuordnen } = mod;
  let gemerkt = gruppenZuordnen([['Kita Eltern', 'echt@g.us']], ['Kita Eltern'], {}).merken;
  // echte Gruppe fehlt (verlassen, entfernt), fremde Gruppe gleichen Namens taucht auf
  let r = gruppenZuordnen([['Kita Eltern', 'fremd@g.us']], ['Kita Eltern'], gemerkt);
  assert.deepEqual(r.neu, {});
  gemerkt = r.merken;
  // nächste Verbindung: die fremde Gruppe wird weiterhin nicht übernommen
  r = gruppenZuordnen([['Kita Eltern', 'fremd@g.us']], ['Kita Eltern'], gemerkt);
  assert.deepEqual(r.neu, {});
  assert.deepEqual(r.merken, { 'echt@g.us': 'Kita Eltern' });
});
test('Dateien je Gruppe und Tag werden gezählt', () => {
  const { dateienHeute } = mod;
  const l = [{ gruppe: 'A', tag: '2026-11-26', datei: 'x' }, { gruppe: 'A', tag: '2026-11-26' }, { gruppe: 'B', tag: '2026-11-26', datei: 'y' }];
  assert.equal(dateienHeute(l, 'A', '2026-11-26'), 1);
});
