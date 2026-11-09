# paenz-abruf

Nächtlicher Abruf der Quellen für den Pänz-Kalender (Kinder-Termine 0–6 Jahre in Köln), **ohne KI**.
Die GitHub Action `.github/workflows/abruf.yml` läuft täglich um 03:30 UTC (04:30 Winter / 05:30 Sommer)
und per Hand über *Actions → Quellen abrufen → Run workflow*. Sie legt das Ergebnis in `out/` ab und
committet. Die Git-Historie ist damit das Archiv.

## Ausgabe

| Datei | Inhalt |
|---|---|
| `out/heute.md` | **Tageszusammenfassung.** Erste Zeile `STAND: YYYY-MM-DDTHH:MM` (Europe/Berlin). Pro geänderter Quelle nur neue Zeilen/Termine, unveränderte als Sammelzeile, Fehler/Sperren als Liste. |
| `out/quellen/<id>.md` | bereinigter Text je Quelle, ein Abschnitt `## Quelle: <url>` pro URL, Links als `[Text](URL)` |
| `out/quellen/<id>.events.json` | schema.org-Termine (JSON-LD/Microdata), falls die Seite welche hat |
| `out/status.json` | je Quelle: Status, URLs, HTTP-Status, Bytes, sha256, geändert, Fehler, letzter Erfolg |
| `out/wetter.json` | 10-Tage-Vorhersage Köln von MET Norway (yr.no) |

Status je Quelle: `ok`, `teilweise` (eine von mehreren URLs fehlgeschlagen, deren alter Stand bleibt),
`fehler`, `blocked` (robots.txt, HTTP 401/403/429/451 oder Bot-Schutz; wird nicht umgangen), `skip`.
Bei Fehlern bleibt der letzte gute Stand in `out/quellen/` liegen; der Vergleich läuft immer gegen den
letzten **erfolgreichen** Abruf.

`★` vor einer Zeile = Kleinkind-Stichwort gefunden (Kinder, Familie, Baby, Krabbel, Kita, ab 2 J., Puppen,
Kasper, Märchen, Laterne, Martin, Nikolaus, Flohmarkt, Basar, Mitmach …). Es wird nichts weggefiltert.
Die Liste steht in `scraper/extract.py` (`_KIDS`).

## Quelle hinzufügen

`config/quellen.yml` ist maßgeblich. Neuen Eintrag anhängen:

```yaml
- id: mein-theater               # eindeutig, wird Dateiname in out/quellen/
  name: Mein Theater
  group: Musik & Bühne
  url: https://www.mein-theater.de/spielplan/
  note: Freitext für den Morgenlauf
```

Optionale Felder:

| Feld | Bedeutung |
|---|---|
| `urls` | weitere Unterseiten, Liste aus URLs oder Objekten `{url: …, selector: …, paginate: …, js: …, typ: …}` |
| `selector` | CSS-Selektor für den Bereich mit der Terminliste (nur für die Haupt-URL; Unterseiten eigene `selector`) |
| `typ` | `html` (Standard), `pdf` (URL ist ein PDF), `nippes_pdf` (Sonderfall Freizeitkalender Nippes) |
| `paginate` | `{param: start, step: 20, max_seiten: 15}` hängt `?start=20`, `?start=40` … an, bis keine neuen Zeilen mehr kommen |
| `js` | `true`: Seite mit Playwright rendern (nur wenn die Termine erst per JavaScript kommen; kostet ca. 1 Min. Installation) |
| `js_klick`, `js_klick_max` | bei `js: true`: Selektor für „Mehr laden“ und wie oft geklickt wird, z. B. `"text=Mehr laden"`, `3` |
| `ab`, `bis` | regulärer Ausdruck: Text erst ab der ersten passenden Zeile behalten bzw. vor der nächsten passenden Zeile abschneiden (z. B. `ab: "^November 2026$"`-artige Monatsüberschriften); wirkt nur auf die Haupt-URL, Unterseiten eigene Werte |
| `folge_pdf` | regulärer Ausdruck für PDF-Links auf der Seite; bis zu 2 passende PDFs werden mitgelesen (z. B. Halbjahres-Programmhefte mit wechselndem Dateinamen) |
| `ignoriere` | Liste regulärer Ausdrücke; passende Zeilen werden verworfen (gegen tägliches Rauschen wie Uhrzeiten, Zähler, Zufalls-Teaser) |
| `skip` | Grund als Text; Quelle wird nicht abgerufen und als `skip` gemeldet |

Lokal testen (Python 3.12):

```bash
pip install -r requirements.txt
python -m scraper.main --nur mein-theater --ohne-wetter
```

## Selektoren setzen

Ohne Selektor nimmt der Scraper `main`, `[role=main]`, `#content`, `#main`, `.content`, `#inhalt`
oder ein einzelnes `article`, sonst `body`. Navigation, Header/Footer, Cookie-Banner, Sidebar,
Teilen-Leisten, Skripte, Bilder und Formularelemente werden vorher entfernt.

Wenn trotzdem zu viel Rauschen in `out/quellen/<id>.md` steht:

1. Seite im Browser öffnen, Rechtsklick auf die Terminliste → *Untersuchen*.
2. Das Element suchen, das alle Termine umschließt, z. B. `<div class="event-list">`.
3. In der Konfiguration `selector: ".event-list"` setzen. Mehrere Bereiche mit Komma:
   `selector: "#spielplan, .termine"`.
4. Wenn der Selektor nichts findet, steht das als Hinweis in `out/status.json` und es gilt der Fallback.

## Regeln

- Eigener User-Agent `PaenzKalenderBot/1.0 (privater Familienkalender; Kontakt über GitHub rosnokis)`,
  mind. 1,5 s Pause zwischen Anfragen, robots.txt (inkl. Crawl-delay bis 10 s) wird beachtet.
- Abgerufene Inhalte gelten als untrusted: nur geparst, nie ausgeführt. Nur HTML und erwartete PDFs,
  max. 5 MB und 30 s pro Antwort. E-Mail-Adressen im Text werden durch `[E-Mail]` ersetzt.
- Keine Secrets, keine Anmeldungen, keine Umgehung von Sperren. Keine persönlichen Daten im Repository.
