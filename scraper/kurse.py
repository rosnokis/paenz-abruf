"""Baut out/kurse/kurse.json aus den Samstags-Abrufen der Kursanbieter.

Ausgewertet werden die webbasys-Listen von FamilienForum Köln und Bildungswerk der Erzdiözese
(out/kurse/quellen/*.md). Andere Anbieter (z. B. fbs) pflegt die Sonntagsaufgabe von Hand nach.
Format: {"stand", "anbieter": [...], "kurse": [{id,title,anbieter,art,mmin,mmax,place,veedel,
wd,t,start,ende,termine,pk,url,note,added,geprueft}]}; mmin/mmax = Alter in Monaten.
"added" bleibt aus der vorigen Datei erhalten, damit neue Kurse erkennbar sind.

Aufruf:  python -m scraper.kurse [--heute JJJJ-MM-TT]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
QUELLEN = {
    "ff": ("familienforum-koeln", "FamilienForum Köln", None),
    "bw": ("bildungswerk-erzdioezese-koeln", "Bildungswerk der Erzdiözese Köln",
           "https://programm.bildungswerk-ev.de/bw-koeln/kurs/"),
}
WDN = {"So": 0, "Mo": 1, "Di": 2, "Mi": 3, "Do": 4, "Fr": 5, "Sa": 6}
VEEDEL = [("Weißenburg", "Agnesviertel"), ("Arnold-von", "Südstadt"), ("Severin", "Südstadt"),
          ("Rotkehlchen", "Vogelsang"), ("St. Urba", "Mülheim"), ("Havelstr", "Chorweiler"),
          ("Nikolaus", "Sülz"), ("Regenbold", "Volkhoven/Weiler"), ("Maria Himmelfahrt", "Holweide"),
          ("Decksteiner", "Klettenberg"), ("Pfarrer-Mo", "Kalk"), ("Tönnis", "Worringen"),
          ("Heidestr", "Porz"), ("Amandus", "Langel & Rheindörfer"), ("Eiler", "Rath/Heumar"),
          ("Philipp-Wirtge", "Riehl")]
SKIP = ("Vormerkliste", "Schreibwerkstatt", "Kleidertausch", "Erfahrungsaustausch",
        "Erziehung im Kleinkindalter", "Jugendliche", "ab 9", "Kindern von 7", "6-9 Jahre")
OHNE = ("Eltern-Kind-Kurse", "Kita-Kinder", "Kreativ und Praktisch", "Spiel und Bewegung", "Spiel und Musik")


def parse_webbasys(text: str, base: str | None) -> list[dict]:
    """Zerlegt eine webbasys-Liste in Kursblöcke; jeder Block endet mit der 10-stelligen Kursnummer."""
    out, seen = [], set()
    sec = place = price = dr = rh = mode = None
    titles: list[str] = []
    pl: list[str] = []
    for raw in text.splitlines():
        l = raw.strip().lstrip("★").strip()
        if l.startswith("## Quelle:"):
            sec = l.split(":", 1)[1].strip(); place = price = dr = rh = mode = None; titles = []
            continue
        if not l:
            continue
        if re.match(r"^(Mo|Di|Mi|Do|Fr|Sa|So)\. \d\d\.\d\d\.\d{4}", l):
            dr, mode = l, None; continue
        if re.match(r"^(\d+x|\d+ Termine?)\s*\|", l):
            rh = l; continue
        if l == "Ort":
            mode, pl = "ort", []; continue
        if l in ("Gebühr", "Referent/in", "Kursnummer", "Anmeldung"):
            mode = l; continue
        if re.match(r"^\d{10}$", l):
            if l not in seen:
                seen.add(l)
                out.append(dict(src=sec, nr=l, title=" / ".join(titles[-2:]), dr=dr, rh=rh,
                                place=place, price=price, url=(base + l) if base else sec))
            titles, mode = [], None
            continue
        if re.match(r"^[\d.,]+ €$", l) or l == "gebührenfrei":
            price, mode = l, None; continue
        if (re.match(r"^\d\d\.\d\d\.\d{4}$", l) or "Warenkorb" in l or l.startswith("[zur Detailseite")
                or l == "keine Online-Anmeldung" or l.startswith(("Vorherige", "Nächste"))
                or re.match(r"^\d+ bis \d+ von", l)):
            continue
        if mode == "ort":
            pl.append(l)
            if re.match(r"^5\d{4}", l):
                place, mode = " ".join(pl).rstrip(","), None
            continue
        if mode in ("Referent/in", "Anmeldung"):
            mode = None; continue
        if l.startswith(("[", "Kursangebote", "/ ")):
            continue
        if re.match(r"^5\d{4} ", l) and titles:
            place = " ".join(titles[-2:] + [l]); titles = titles[:-2]; continue
        titles.append(l)
    return out


def _d(s: str) -> dt.date:
    dd, mm, yy = s.split(".")
    return dt.date(int(yy), int(mm), int(dd))


def _monate(m: int, y: int, ref: dt.date) -> int:
    return (ref.year - y) * 12 + ref.month - m


def alter(t: str, start: dt.date) -> tuple[int | None, int | None]:
    """Altersspanne in Monaten zum Kursstart, soweit aus dem Titel lesbar."""
    t = t.replace(",", ".")
    m = re.search(r"geboren (\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{4})", t)
    if m:
        m1, y1, m2, y2 = map(int, m.groups())
        return max(0, _monate(m2, y2, start)), _monate(m1, y1, start) + 6
    m = re.search(r"geboren (\d{1,2})\s*-\s*(?:Mitte )?(\d{1,2})/(\d{2,4})", t)
    if m:
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
        return max(0, _monate(int(m.group(2)), y, start)), _monate(int(m.group(1)), y, start) + 6
    m = re.search(r"(\d+(?:\.\d)?)\s*(?:-|bis|und)\s*(\d+(?:\.\d)?)\s*Monat", t)
    if m:
        return int(float(m.group(1))), int(float(m.group(2)))
    m = re.search(r"(\d+(?:\.\d)?)\s*(?:-|bis|und)\s*(\d+(?:\.\d)?)\s*[jJ]", t)
    if m:
        return int(float(m.group(1)) * 12), int(float(m.group(2)) * 12) + 11
    m = re.search(r"ab (\d+(?:\.\d)?)\s*J", t)
    if m:
        return int(float(m.group(1)) * 12), 83
    if any(k in t for k in ("ersten Lebensjahr", "erstes Jahr", "erste Lebensjahr", "1. Lebensjahr")):
        return 0, 12
    if "zweite und dritte" in t:
        return 12, 36
    if "zweites Jahr" in t or "zweiten Lebensjahr" in t:
        return 12, 24
    if "1-2-3" in t:
        return 0, 36
    m = re.search(r"(\d)-(\d)$", t)
    if m:
        return int(m.group(1)) * 12, int(m.group(2)) * 12 + 11
    return None, None


def art(t: str) -> str:
    tl = t.lower()
    if "massage" in tl:
        return "Sonstiges"
    if any(k in tl for k in ("schwimm", "wasser")):
        return "Schwimmen"
    if any(k in tl for k in ("musik", "rhythm", "töne", "singen, erz")) and "spielen, singen" not in tl:
        return "Musik"
    if any(k in tl for k in ("tanz", "yoga", "beweg", "moves", "turn")):
        return "Turnen & Bewegung"
    if any(k in tl for k in ("gips", "filz", "näh", "ton", "perlen", "druck", "meisterwerk", "kürbis", "kreativ", "stoff")):
        return "Kreativ"
    if any(k in tl for k in ("baby", "lebensjahr", "pikler", "kidix", "geboren", "0 bis 1")):
        return "Baby & PEKiP"
    return "Spielgruppe"


def kurs(f: str, anb: str, x: dict, heute: dt.date) -> dict | None:
    t, dr = x["title"], x["dr"] or ""
    if any(s in t for s in SKIP) or not dr:
        return None
    m = re.match(r"(\w\w)\. (\d\d\.\d\d\.\d{4})(?: - \w\w\. (\d\d\.\d\d\.\d{4}))?(?:, ([\d.]+ - [\d.]+) Uhr)?", dr)
    if not m or m.group(1) not in WDN:
        return None
    st = _d(m.group(2)); en = _d(m.group(3)) if m.group(3) else st
    if en < heute or (st < heute and (en - heute).days < 42):
        return None  # vorbei oder läuft schon fast zu Ende
    if m.group(4):
        tm, n = m.group(4), 1
    else:
        r = re.match(r"(\d+)(?:x| Termine?)\s*\|\s*([\d.]+ - [\d.]+) Uhr", x["rh"] or "")
        if not r:
            return None
        n, tm = int(r.group(1)), r.group(2)
    tm = re.sub(r"\b(\d):", r"0\1:", tm.replace(".", ":").replace(" - ", "–"))
    parts = [p for p in t.split(" / ") if p not in OHNE]
    a0, a1 = alter(t, max(st, heute))
    if a0 is not None and a0 > 72:
        return None
    pr = x["price"]
    pk = None
    if pr == "gebührenfrei":
        pk = 0
    elif pr:
        pk = float(pr.replace(" €", "").replace(".", "").replace(",", "."))
        pk = int(pk) if pk == int(pk) else pk
    place = (x["place"] or "").replace(" ,", ",")
    k = dict(id=f + "-" + x["nr"], title=" – ".join(parts) if parts else t, anbieter=anb, art=art(t),
             mmin=a0, mmax=a1, place=place or None, veedel=next((v for s, v in VEEDEL if s in place), None),
             wd=[WDN[m.group(1)]], t=tm, start=st.isoformat(), ende=en.isoformat(), termine=n, pk=pk,
             price=None if pk is not None else "Preis siehe Link", url=x["url"],
             note=("Kursnr. " + x["nr"] + " (auf der Seite suchen)") if f == "ff" else None)
    return {a: b for a, b in k.items() if b is not None}


def bauen(root: Path, heute: dt.date, jetzt: str) -> dict:
    ziel = root / "out/kurse/kurse.json"
    alt = {}
    if ziel.exists():
        alt = {k["id"]: k for k in json.loads(ziel.read_text(encoding="utf-8")).get("kurse", [])}
    kurse = []
    for f, (qid, anb, base) in QUELLEN.items():
        p = root / "out/kurse/quellen" / (qid + ".md")
        if not p.exists():
            continue
        for x in parse_webbasys(p.read_text(encoding="utf-8"), base):
            k = kurs(f, anb, x, heute)
            if k:
                k["added"] = alt.get(k["id"], {}).get("added", heute.isoformat())
                k["geprueft"] = heute.isoformat()
                kurse.append(k)
    anbieter = json.loads((root / "config/kurse_anbieter.json").read_text(encoding="utf-8"))
    return {"stand": jetzt, "anbieter": anbieter, "kurse": kurse}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heute")
    a = ap.parse_args()
    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    heute = dt.date.fromisoformat(a.heute) if a.heute else now.date()
    data = bauen(ROOT, heute, now.strftime("%Y-%m-%dT%H:%M"))
    ziel = ROOT / "out/kurse/kurse.json"
    ziel.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data['kurse'])} Kurse in {ziel.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
