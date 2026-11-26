# paenz-abruf

Nächtlicher Abruf der Quellen für den Pänz-Kalender (Kinder-Termine 0–6 Jahre in Köln), **ohne KI**.
Die GitHub Action `.github/workflows/abruf.yml` startet nachts zu fünf Zeiten zwischen 01:17 und 04:07 UTC,
weil GitHub geplante Läufe oft verspätet startet. Der erste Lauf des Tages ruft die Quellen ab, spätere Läufe
am selben Tag brechen sofort ab. Per Hand: *Actions → Quellen abrufen → Run workflow* (ruft immer ab).
Die Action legt das Ergebnis in `out/` ab und committet. Die Git-Historie ist damit das Archiv.
Samstags ruft `.github/workflows/kurse.yml` die Kursanbieter ab (`out/kurse/`).
Der Morgenlauf des Pänz-Kalenders (geplante Claude-Aufgabe, 06:16 Uhr) liest dieses Repository.

## Ausgabe

| Datei | Inhalt |
|---|---|
| `out/heute.md` | **Tageszusammenfassung.** Erste Zeile `STAND: YYYY-MM-DDTHH:MM` (Europe/Berlin). Pro geänderter Quelle nur neue Zeilen/Termine, unveränderte als Sammelzeile, Fehler/Sperren als Liste. |
| `out/quellen/<id>.md` | bereinigter Text je Quelle, ein Abschnitt `## Quelle: <url>` pro URL, Links als `[Text](URL)` |
| `out/quellen/<id>.events.json` | schema.org-Termine (JSON-LD/Microdata), falls die Seite welche hat |
| `out/status.json` | je Quelle: Status, URLs, HTTP-Status, Bytes, sha256, geändert, Fehler, letzter Erfolg |
| `out/wetter.json` | 10-Tage-Vorhersage Köln von MET Norway (yr.no) |
| `out/details/index.json` + `out/details/<hash>.md` | **Detailseiten.** Die in neuen Zeilen/Terminen verlinkten Veranstaltungsseiten, bereinigt als Text (Kopf: `URL`, `ABGERUFEN`, `VON`). `index.json` ordnet URL → Datei zu. Höchstens 60 pro Nacht, 12 je Quelle, dieselbe Seite frühestens nach 3 Tagen erneut, nach 30 Tagen gelöscht. |
| `out/tickets.json` | **Ticketstatus** je Seite: `s` = `ausverkauft` / `knapp` / `tageskasse` / `verfuegbar` / `unklar`, `txt` = die Fundzeile, `geprueft`. Geprüft werden jede Nacht die Seiten aus `config/tickets.yml` und nebenbei alle geladenen Detailseiten. Reine Stichwortsuche, also ein Hinweis, kein Beweis. |
| `out/kandidaten.md` | **Veranstalter-Kandidaten:** Domains, die auf Übersichtsquellen (`harvest: true`) in ★-Zeilen verlinkt sind und noch keine Quelle sind. Jede Domain erscheint einmal (`out/kandidaten-gesehen.json`), höchstens 40 pro Nacht. |

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

Weitere Felder je Quelle: `harvest: true` (Übersichtsseite: fremde Veranstalter-Links landen in
`out/kandidaten.md`), `details: false` (verlinkte Detailseiten dieser Quelle nicht vorab laden).

Zeilen, die sich täglich ändern, ohne etwas zu sagen (Trefferzähler, Namen sozialer Netzwerke,
„Seite 2 von 9“, „Stand: 10.11.2026“), werden bei allen Quellen verworfen (`GLOBAL_IGNORE` in
`scraper/main.py`); Besonderheiten einer Quelle kommen in deren `ignoriere`.

Ticketseiten stehen in `config/tickets.yml` (`url`, optional `note`, `bis` = letzter Termin, `js: true`).
Nur Seiten zu einem einzelnen Termin eintragen. Nach `bis` wird eine Seite nicht mehr geprüft und in
`out/heute.md` als abgelaufen genannt. Bleibt der Status einer Ticketseite unklar, folgt der Scraper
einmal dem Link zum Shop bzw. zur Anmeldung (Feld `ueber` in `out/tickets.json`; bleibt es unklar, nennt `folge` den Grund). Stehen auf einer Seite
ausverkaufte und buchbare Termine, lautet der Status `teilweise`.

## Pflege per Werkzeug

`python -m scraper.pflege` ist der einzige Weg, auf dem der automatische Morgenlauf dieses Repository
ändert, und zwar nur `config/tickets.yml` und `config/quellen.yml`:

```bash
python -m scraper.pflege ticket https://… --note "Titel, Datum" --bis 2026-12-06 [--js]
python -m scraper.pflege quelle --name "Name" --url https://… [--group "…"] [--note "…"] [--harvest] [--js]
python -m scraper.pflege aufraeumen   # abgelaufene Ticketseiten entfernen
python -m scraper.pflege pruefe       # beide Dateien prüfen
```

Das Werkzeug erzwingt: nur https, keine Platzhalter-Adressen, keine Dubletten, kurze Notizen ohne
Adressen, höchstens 6 Ticketseiten und 4 Quellen pro Tag. Die nächtliche Action prüft die Konfiguration
vor dem Abruf und nimmt bei einem Fehler den letzten gültigen Stand.

## Fehler und Ausfälle

Dauerhaft gesperrte Quellen bekommen in `config/quellen.yml` das Feld `selten: <Grund>`. Sie bleiben notiert,
werden aber nur montags erneut versucht und stehen an den übrigen Tagen als übersprungen in `out/heute.md`.

`out/status.json` führt je Quelle `fehler_seit` und `fehler_tage`. Ab drei Tagen ohne Erfolg steht die
Quelle in `out/heute.md` unter „Dauerfehler“ (am dritten Tag mit dem Zusatz NEU).

## Tests

```bash
python -m unittest discover -s tests -v
```

`tests/test_fetch.py` prüft die Sperre interner Adressen mit einem lokalen Testserver. Die Browser-Tests darin
laufen nur, wenn Playwright installiert ist (`pip install -r requirements-js.txt`), sonst überspringt sie der Test.

Die Tests arbeiten mit einem Attrappen-Abruf in einem Temp-Ordner (`--root`), nie in `config/` oder
`out/`. Der echte Abruf weigert sich, Platzhalter-Adressen (`.example`, `localhost` …) abzurufen, und
ein Test schlägt fehl, wenn solche Adressen in `out/` oder `config/` stehen.

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

## Sicherheit (Audit vom 26.11.2026)

- **Keine internen Adressen (SSRF):** Vor jeder Anfrage prüft `scraper/fetch.py` (`address_problem`) das Schema
  und alle DNS-Adressen des Hosts. Erlaubt sind nur `http(s)` und nur öffentliche Adressen. Gesperrt sind
  Loopback, private Netze, Link-Local (z. B. der Metadaten-Dienst `169.254.169.254`), Carrier-NAT, IPv6 ULA
  und IPv4-mapped IPv6. Eine gesperrte Adresse steht als `gesperrt: …` in `out/status.json`.
- **Weiterleitungen:** Der Fetcher folgt Weiterleitungen selbst und prüft jede einzeln, höchstens 5.
  Im Browser (Playwright) läuft jede Anfrage durch dieselbe Prüfung. Weiterleitungen der Hauptseite folgt
  `render()` selbst, Frames folgen keinen Weiterleitungen.
- **Token nur beim Push:** Alle Workflows checken mit `persist-credentials: false` aus. Den Token mit
  Schreibrecht setzt erst der Schritt „Ergebnis committen“. Während Playwright fremde Seiten lädt,
  liegt kein Token im Checkout.
- **Bekannte Grenze:** Zwischen Prüfung und Verbindung löst das System den Namen erneut auf (DNS-Rebinding).
  Auf GitHub-Runnern gibt es kaum interne Ziele.

## Pänz-Sammler (Synology, `nas/paenz-sammler`)

Der Sammler liest freigegebene WhatsApp-Gruppen über ein verknüpftes Gerät mit. Er sammelt Flyer (Bilder,
PDFs) und Nachrichten mit Datum oder Link und schickt einmal am Tag eine Sammelmail. Er schreibt nie in
Gruppen. Absender und Telefonnummern stehen nicht in der Mail. Stand 26.11.2026: noch nicht in Betrieb.

Einrichten:

1. Kopieren Sie `env.beispiel` als `einstellungen.env` und füllen Sie die Werte aus. Laden Sie
   `einstellungen.env` nie ins Repository hoch.
2. Starten Sie den Container im Ordner `nas/paenz-sammler`: `docker compose up -d --build`.
3. Öffnen Sie `data/qr.png` und scannen Sie den QR-Code mit dem Zweithandy
   (WhatsApp → Verknüpfte Geräte → Gerät hinzufügen).
4. Prüfen Sie im Log die Zeilen „Gruppe fest zugeordnet“ und „Ausgewertet“.

Verhalten:

- **Gruppen fest über die ID:** Beim ersten Treffer merkt sich der Sammler die feste ID jeder Gruppe aus
  `GRUPPEN` (`data/status.json`, Feld `gruppen`). Danach zählt nur die ID. Eine fremde Gruppe mit gleichem
  Namen oder eine umbenannte Gruppe liest er nicht mit. Haben beim ersten Start zwei Gruppen denselben
  Namen, wertet er keine davon aus und schreibt eine Warnung ins Log. Neu zuordnen: Feld `gruppen` in
  `data/status.json` löschen und den Container neu starten.
- **Dateigröße:** Bilder und PDFs lädt der Sammler nur bis `MAX_DATEI_MB` (Standard 15 MB). Größere Dateien
  stehen nur als Hinweis in der Mail.
- **Ohne root:** Der Container startet als root, übergibt `data/` an den Benutzer `node` (uid 1000) und
  läuft danach nur mit dessen Rechten. `docker-compose.yml` entfernt alle Capabilities außer den nötigen
  und setzt `no-new-privileges`.
- **Neu koppeln:** Meldet der Sammler „neu koppeln nötig“, löschen Sie den Ordner `data/anmeldung`,
  starten den Container neu und scannen den neuen QR-Code.
- **Zugangsdaten:** `data/anmeldung` enthält die WhatsApp-Sitzung mit vollem Zugriff auf das Konto.
  Geben Sie den Ordner `data/` auf der Synology nur für Ihr eigenes Konto frei.

Tests: `npm ci && npm test` im Ordner `nas/paenz-sammler` (startet keine WhatsApp-Verbindung).
