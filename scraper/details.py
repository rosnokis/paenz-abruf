"""Nightly extras on top of the source fetch (all without AI):

- details:    pre-fetch the event pages linked from NEW lines/events, so the morning run can
              confirm date, time, price and age from local files instead of fetching them itself.
- tickets:    availability keywords on the pages from config/tickets.yml and on all detail pages.
- kandidaten: organiser domains seen on overview sources that are not a source yet.

Everything fetched is untrusted data: parsed to text, never executed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import extract

MAX_DETAILS = 60          # detail pages per night
MAX_PER_SOURCE = 12
REFRESH_DAYS = 3          # do not fetch the same detail page again earlier than this
PRUNE_DAYS = 30
TICKET_PRUNE_DAYS = 21
MAX_KANDIDATEN = 40       # listed per night; the rest waits for the next night
MAX_DETAIL_LINES = 400

LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# Never worth a request: social media, maps, app stores, trackers, big platforms.
SKIP_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "tiktok.com",
    "linkedin.com", "pinterest.com", "pinterest.de", "whatsapp.com", "wa.me", "t.me", "threads.net",
    "google.com", "google.de", "goo.gl", "maps.app.goo.gl", "apple.com", "spotify.com", "amazon.de",
    "wikipedia.org", "openstreetmap.org", "bit.ly", "linktr.ee", "paypal.com", "vimeo.com",
    "soundcloud.com", "mailchi.mp", "xing.com", "bahn.de", "kvb.koeln", "vrs.de",
)
# Not organisers (ticket shops, city portals, media) -> never a "Kandidat".
NO_KANDIDAT = (
    "eventim.de", "koelnticket.de", "reservix.de", "eventbrite.de", "eventbrite.com", "ticketmaster.de",
    "bonnticket.de", "westticket.de", "adticket.de", "pretix.eu", "eveeno.com", "rausgegangen.de",
    "ksta.de", "rundschau-online.de", "express.de", "wdr.de", "t-online.de", "nrw-tourismus.de",
)
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".ics", ".mp3", ".mp4", ".doc",
            ".docx", ".xls", ".xlsx")

SHOP_TEXT = re.compile(r"tickets?|karten|anmeld|buchen|buchung|reservier|vorverkauf|buy|book|register", re.I)
RESERVED_HOST = re.compile(r"(^|\.)(example|test|invalid|localhost)$|(^|\.)example\.(com|org|net)$|^localhost$|"
                           r"^\d+\.\d+\.\d+\.\d+$")

TICKET_RULES = [  # first match wins, strongest first
    ("ausverkauft", re.compile(r"ausverkauft|ausgebucht|sold\s*out|keine\s+(karten|tickets|plätze)\s+mehr|"
                               r"restlos\s+vergriffen|fully\s+booked|no\s+tickets\s+available", re.I)),
    ("knapp", re.compile(r"warteliste|restkarten|restplätze|wenige\s+(rest)?(karten|tickets|plätze)|"
                         r"fast\s+ausverkauft|nur\s+noch\s+\d+\s+(karten|tickets|plätze)|waiting\s+list|"
                         r"(remaining|few|last)\s+tickets", re.I)),
    ("tageskasse", re.compile(r"nur\s+(an\s+der\s+)?(tages|abend)kasse|kein\s+vorverkauf", re.I)),
    ("verfuegbar", re.compile(r"(tickets?|karten)\s+(kaufen|buchen|bestellen|sichern|verfügbar|erhältlich)|"
                              r"jetzt\s+buchen|in\s+den\s+warenkorb|zum\s+ticketshop|plätze\s+frei|"
                              r"anmeldung\s+möglich|zur\s+anmeldung|ticketlink|tickets:|buy\s+tickets|book\s+(now|tickets)|"
                              r"add\s+to\s+(cart|basket)", re.I)),
]


def domain(url: str) -> str:
    host = urlsplit(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _in(dom: str, group: tuple[str, ...] | set[str]) -> bool:
    return any(dom == d or dom.endswith("." + d) for d in group)


def links_in(lines: list[str]) -> list[tuple[str, str, str]]:
    """(text, url, line) for every markdown link."""
    return [(m.group(1).strip(), m.group(2), l) for l in lines for m in LINK.finditer(l)]


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _older(stamp: str | None, days: int, today: date) -> bool:
    try:
        return date.fromisoformat((stamp or "")[:10]) < today - timedelta(days=days)
    except ValueError:
        return True


def ticket_status(lines: list[str]) -> tuple[str, str]:
    hits: dict[str, str] = {}
    for status, pat in TICKET_RULES:
        for l in lines:
            if pat.search(l):
                hits[status] = l.removeprefix(extract.MARK)[:160]
                break
    if "ausverkauft" in hits and "verfuegbar" in hits:
        # several dates on one page: some sold out, some bookable
        return "teilweise", f"{hits['ausverkauft']} / {hits['verfuegbar']}"[:200]
    for status, _ in TICKET_RULES:
        if status in hits:
            return status, hits[status]
    return "unklar", ""


def is_placeholder(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return bool(RESERVED_HOST.search(host))


def shop_link(lines: list[str], page_url: str) -> str | None:
    """The first link on a ticket page that looks like the way into the shop or sign-up form."""
    for text, url, _ in links_in(lines):
        if url.split("#")[0] == page_url.split("#")[0] or _in(domain(url), SKIP_DOMAINS) or is_placeholder(url):
            continue
        if urlsplit(url).path.lower().endswith(SKIP_EXT + (".pdf",)):
            continue
        if SHOP_TEXT.search(text):
            return url
    return None


def fetch_page(f, url: str, js: bool = False) -> tuple[list[str] | None, str | None]:
    """Return (lines, error). Events found on the page are appended as formatted lines."""
    is_pdf = urlsplit(url).path.lower().endswith(".pdf")
    r = f.render(url) if js else f.get(url, "pdf" if is_pdf else "html")
    if not r.ok:
        return None, r.error
    try:
        if r.ctype in ("application/pdf", "application/x-pdf"):
            lines, _ = extract.pdf_to_lines(r.content)
        else:
            lines, events, _ = extract.html_to_lines(r.content, r.final_url)
            lines += extract.button_texts(r.content)  # "Ausverkauft" often only sits on a button
            for e in events[:20]:
                bits = [e.get("startDate"), e.get("name"), (e.get("location") or {}).get("name"),
                        (e.get("location") or {}).get("address")]
                bits += [f"{o.get('price')} {o.get('priceCurrency', '')}".strip() for o in (e.get("offers") or [])[:3]]
                bits += [e.get("typicalAgeRange"), e.get("audience"), e.get("eventStatus")]
                lines.append("Termin (strukturiert): " + " | ".join(str(b) for b in bits if b))
    except Exception as exc:  # hostile/broken input: report, don't crash
        return None, f"Parserfehler: {type(exc).__name__}"
    if not lines:
        return None, "kein Text gefunden"
    return lines[:MAX_DETAIL_LINES], None


# ======================================================================= details + tickets
def run_details(f, results: list[dict], sources: dict, out: Path, stand: datetime, root: Path) -> dict:
    today = stand.date()
    stamp = f"{stand:%Y-%m-%dT%H:%M}"
    ddir = out / "details"
    ddir.mkdir(parents=True, exist_ok=True)
    index: dict = _read(ddir / "index.json", {})
    tickets: dict = _read(out / "tickets.json", {}).get("seiten", {})
    own_pages = {u for s in sources.values() for u in [s["url"]] + [
        e if isinstance(e, str) else e["url"] for e in s.get("urls", []) or []]}

    def wanted(url: str) -> bool:
        if url in own_pages or _in(domain(url), SKIP_DOMAINS):
            return False
        if urlsplit(url).path.lower().endswith(SKIP_EXT):
            return False
        return _older(index.get(url, {}).get("abgerufen"), REFRESH_DAYS, today)

    # 1) queue: ticket pages first (always), then links from new lines/events of changed sources
    queue: list[tuple[str, str]] = []
    try:
        tcfg = yaml.safe_load((root / "config" / "tickets.yml").read_text(encoding="utf-8")) or {}
    except OSError:
        tcfg = {}
    entries = [t if isinstance(t, dict) else {"url": t} for t in tcfg.get("seiten", []) or []]
    entries = [t for t in entries if isinstance(t.get("url"), str) and t["url"].startswith("https://")
               and not is_placeholder(t["url"])]
    expired = [t["url"] for t in entries if t.get("bis") and str(t["bis"])[:10] < today.isoformat()]
    entries = [t for t in entries if t["url"] not in expired]
    ticket_urls = [t["url"] for t in entries]
    js_urls = {t["url"] for t in entries if t.get("js")}
    queue += [(u, "tickets.yml") for u in ticket_urls]
    for r in results:
        if not r.get("geaendert") or r.get("erstabruf") or sources[r["id"]].get("details") is False:
            continue
        urls = [e["url"] for e in r.get("neue_termine", []) if e.get("url")]
        urls += [u for _, u, _ in links_in(r.get("neue_zeilen", []))]
        picked = []
        for u in urls:
            if u not in picked and wanted(u) and not is_placeholder(u):
                picked.append(u)
        queue += [(u, r["id"]) for u in picked[:MAX_PER_SOURCE]]

    # 2) fetch
    fetched = failed = 0
    seen: set[str] = set()
    for url, von in queue:
        if url in seen:
            continue
        seen.add(url)
        if fetched >= MAX_DETAILS + len(ticket_urls):
            break
        lines, err = fetch_page(f, url, url in js_urls)
        name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12] + ".md"
        if err:
            failed += 1
            index[url] = {**index.get(url, {}), "abgerufen": stamp, "von": von, "fehler": err}
            if url in ticket_urls:
                tickets[url] = {**tickets.get(url, {}), "fehler": err, "versucht": stamp}
            continue
        fetched += 1
        (ddir / name).write_text("\n".join([f"URL: {url}", f"ABGERUFEN: {stamp}", f"VON: {von}", ""] + lines) + "\n",
                                 encoding="utf-8")
        index[url] = {"datei": f"out/details/{name}", "abgerufen": stamp, "von": von, "zeilen": len(lines)}
        s, txt = ticket_status(lines)
        entry = {"s": s, "txt": txt, "geprueft": stamp,
                 "art": "ticketliste" if url in ticket_urls else "detailseite"}
        if url in ticket_urls and s == "unklar":
            # one step further: the status often only shows in the linked shop / sign-up form
            hop = shop_link(lines, url)
            if hop and hop not in seen:
                seen.add(hop)
                hop_lines, hop_err = fetch_page(f, hop, False)
                if hop_lines:
                    hs, htxt = ticket_status(hop_lines)
                    if hs != "unklar":
                        entry.update(s=hs, txt=htxt, ueber=hop)
        if url in ticket_urls or s != "unklar":
            tickets[url] = entry

    # 3) prune
    for url in [u for u, v in index.items() if _older(v.get("abgerufen"), PRUNE_DAYS, today)]:
        datei = index.pop(url).get("datei")
        if datei and (root / datei).exists():
            (root / datei).unlink()
    for url in [u for u in tickets if u in expired or is_placeholder(u)]:
        tickets.pop(url)
    for url in [u for u, v in tickets.items()
                if u not in ticket_urls and _older(v.get("geprueft") or v.get("versucht"), TICKET_PRUNE_DAYS, today)]:
        tickets.pop(url)
    _write(ddir / "index.json", index)
    _write(out / "tickets.json", {"stand": stamp, "seiten": tickets})
    return {"details": fetched, "details_fehler": failed, "tickets": len(tickets), "tickets_abgelaufen": expired}


# ======================================================================= kandidaten
def run_kandidaten(sources: dict, out: Path, stand: datetime) -> dict:
    """New organiser domains linked from sources marked `harvest: true`."""
    today = f"{stand:%Y-%m-%d}"
    seen_path = out / "kandidaten-gesehen.json"
    seen: dict = _read(seen_path, {})
    known = set()
    for s in sources.values():
        for u in [s["url"]] + [e if isinstance(e, str) else e["url"] for e in s.get("urls", []) or []]:
            known.add(domain(u))

    found: dict[str, dict] = {}
    for sid, src in sources.items():
        if not src.get("harvest"):
            continue
        path = out / "quellen" / f"{sid}.md"
        if not path.exists():
            continue
        own = domain(src["url"])
        for text, url, line in links_in(path.read_text(encoding="utf-8").splitlines()):
            dom = domain(url)
            if (not dom or dom == own or dom.endswith("." + own) or own.endswith("." + dom)
                    or _in(dom, known) or _in(dom, SKIP_DOMAINS) or _in(dom, NO_KANDIDAT) or dom in seen):
                continue
            if not extract.is_kids(line):
                continue
            c = found.setdefault(dom, {"domain": dom, "url": url, "text": text, "quellen": [], "n": 0,
                                       "zeile": line.removeprefix(extract.MARK)[:200]})
            c["n"] += 1
            if sid not in c["quellen"]:
                c["quellen"].append(sid)

    ranked = sorted(found.values(), key=lambda c: (-len(c["quellen"]), -c["n"], c["domain"]))
    shown, waiting = ranked[:MAX_KANDIDATEN], len(ranked[MAX_KANDIDATEN:])
    lines = [f"STAND: {stand:%Y-%m-%dT%H:%M}", "# Veranstalter-Kandidaten (noch keine Quelle)", "",
             "Domains, die auf Übersichtsseiten in Zeilen mit Kleinkind-Stichwort verlinkt sind und noch nicht in "
             "config/quellen.yml stehen. Jede Domain erscheint nur einmal (danach in kandidaten-gesehen.json). "
             "Nur Hinweise: vor Aufnahme prüfen.", ""]
    if not shown:
        lines.append("Heute keine neuen Kandidaten.")
    for c in shown:
        lines.append(f"- **{c['domain']}** ({c['n']}× bei {', '.join(c['quellen'])}): "
                     f"[{c['text'] or c['domain']}]({c['url']}) – {c['zeile']}")
        seen[c["domain"]] = {"zuerst": today, "quellen": c["quellen"]}
    if waiting:
        lines += ["", f"… {waiting} weitere warten bis zur nächsten Nacht."]
    (out / "kandidaten.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write(seen_path, seen)
    return {"kandidaten": len(shown), "kandidaten_wartend": waiting}
