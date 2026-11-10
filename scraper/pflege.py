"""Small, strict maintenance tool for the two config files. It is the only way the automated
morning run may change this repository.

  python -m scraper.pflege ticket URL --note "Titel, Datum" --bis JJJJ-MM-TT [--js]
  python -m scraper.pflege quelle --name "Name" --url URL [--group "Gruppe"] [--note "..."] [--harvest] [--js]
  python -m scraper.pflege aufraeumen      # removes expired ticket pages
  python -m scraper.pflege pruefe          # checks both files, exit code 1 on problems

Rules enforced here (not just asked for): https only, no placeholder hosts, no duplicates, short
plain-text notes, at most MAX_TICKETS_TAG / MAX_QUELLEN_TAG additions per day, nothing but
config/tickets.yml and config/quellen.yml is ever written.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml

from .details import domain, is_placeholder

ROOT = Path(__file__).resolve().parent.parent
MAX_TICKETS = 40
MAX_TICKETS_TAG = 6
MAX_QUELLEN_TAG = 4
MAX_NOTE = 140
GROUPS_DEFAULT = "Übersichten"
TICKET_HEAD = """\
# Ticket- und Anmeldeseiten, die jede Nacht auf Verfügbarkeit geprüft werden (Ergebnis: out/tickets.json).
# Nur Seiten zu EINEM Termin eintragen (sonst ist das Stichwort "ausverkauft" nicht eindeutig).
# Felder: url (Pflicht), note (frei), bis (letzter Termin, JJJJ-MM-TT; danach wird die Seite nicht mehr geprüft),
# js: true (Seite braucht JavaScript), seit (Tag der Aufnahme). Pflege per Werkzeug: python -m scraper.pflege
"""
URL_OK = re.compile(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/[^\s\"'<>\\^`{|}]*)?$")
TEXT_BAD = re.compile(r"[\x00-\x1f<>{}`$\\]|https?://|@")


class Problem(Exception):
    pass


def today() -> date:
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def check_url(url: str) -> str:
    url = url.strip()
    if len(url) > 300 or not URL_OK.match(url):
        raise Problem(f"keine gültige https-Adresse: {url[:80]}")
    if is_placeholder(url):
        raise Problem(f"Platzhalter-Adresse: {url[:80]}")
    return url


def check_text(text: str | None, what: str, limit: int = MAX_NOTE) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        raise Problem(f"{what} zu lang (max. {limit} Zeichen)")
    if TEXT_BAD.search(text):
        raise Problem(f"{what} enthält unerlaubte Zeichen, eine Adresse oder ein @")
    return text


def slug(name: str) -> str:
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50].strip("-")


# ----------------------------------------------------------------------- tickets
def load_tickets(root: Path) -> list[dict]:
    data = yaml.safe_load((root / "config" / "tickets.yml").read_text(encoding="utf-8")) or {}
    return [t if isinstance(t, dict) else {"url": t} for t in data.get("seiten", []) or []]


def save_tickets(root: Path, items: list[dict]) -> None:
    order = ("url", "note", "bis", "js", "seit")
    clean = [{k: (str(t[k]) if k in ("bis", "seit") else t[k]) for k in order if t.get(k) not in (None, "", False)}
             for t in items]
    body = yaml.safe_dump({"seiten": clean}, allow_unicode=True, sort_keys=False, width=110)
    (root / "config" / "tickets.yml").write_text(TICKET_HEAD + body, encoding="utf-8")


def cmd_ticket(root: Path, a) -> str:
    url = check_url(a.url)
    note = check_text(a.note, "note")
    try:
        bis = date.fromisoformat(a.bis)
    except (TypeError, ValueError):
        raise Problem("--bis fehlt oder ist kein Datum (JJJJ-MM-TT)")
    if not today() <= bis <= today().replace(year=today().year + 1):
        raise Problem("--bis muss zwischen heute und in einem Jahr liegen")
    items = load_tickets(root)
    if any(t.get("url") == url for t in items):
        return f"schon vorhanden: {url}"
    if len(items) >= MAX_TICKETS:
        raise Problem(f"Ticketliste voll ({MAX_TICKETS}); erst aufraeumen")
    if sum(1 for t in items if str(t.get("seit")) == today().isoformat()) >= MAX_TICKETS_TAG:
        raise Problem(f"Tageslimit erreicht ({MAX_TICKETS_TAG} neue Ticketseiten)")
    items.append({"url": url, "note": note, "bis": bis.isoformat(), "js": bool(a.js), "seit": today().isoformat()})
    save_tickets(root, items)
    return f"Ticketseite aufgenommen: {url}"


def cmd_aufraeumen(root: Path, a) -> str:
    items = load_tickets(root)
    keep = [t for t in items if not (t.get("bis") and str(t["bis"])[:10] < today().isoformat())]
    if len(keep) != len(items):
        save_tickets(root, keep)
    return f"{len(items) - len(keep)} abgelaufene Ticketseiten entfernt, {len(keep)} bleiben"


# ----------------------------------------------------------------------- sources
def load_sources(root: Path) -> list[dict]:
    return (yaml.safe_load((root / "config" / "quellen.yml").read_text(encoding="utf-8")) or {}).get("quellen", [])


def cmd_quelle(root: Path, a) -> str:
    url = check_url(a.url)
    name = check_text(a.name, "name", 80)
    note = check_text(a.note, "note")
    group = check_text(a.group or GROUPS_DEFAULT, "group", 40)
    if len(name) < 3:
        raise Problem("name zu kurz")
    sources = load_sources(root)
    known = {domain(u) for s in sources
             for u in [s["url"]] + [e if isinstance(e, str) else e["url"] for e in s.get("urls", []) or []]}
    if domain(url) in known:
        return f"Domain schon als Quelle vorhanden: {domain(url)}"
    if sum(1 for s in sources if str(s.get("seit")) == today().isoformat()) >= MAX_QUELLEN_TAG:
        raise Problem(f"Tageslimit erreicht ({MAX_QUELLEN_TAG} neue Quellen)")
    sid = slug(name)
    if not sid or sid in {s["id"] for s in sources}:
        raise Problem(f"id nicht eindeutig: {sid!r}")
    entry = {"id": sid, "name": name, "group": group, "url": url}
    if note:
        entry["note"] = note
    if a.harvest:
        entry["harvest"] = True
    if a.js:
        entry["js"] = True
    entry["seit"] = today().isoformat()
    block = yaml.safe_dump([entry], allow_unicode=True, sort_keys=False, width=110)
    path = root / "config" / "quellen.yml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    if len(load_sources(root)) != len(sources) + 1:  # must still parse and contain exactly one more
        path.write_text(text, encoding="utf-8")
        raise Problem("Anhängen hätte die Datei beschädigt, zurückgenommen")
    return f"Quelle aufgenommen: {sid} ({url})"


# ----------------------------------------------------------------------- check
def cmd_pruefe(root: Path, a) -> str:
    probs: list[str] = []
    try:
        sources = load_sources(root)
    except Exception as exc:
        raise Problem(f"quellen.yml nicht lesbar: {type(exc).__name__}")
    ids = set()
    for s in sources:
        sid = s.get("id")
        if not sid or not re.fullmatch(r"[a-z0-9-]{2,60}", str(sid)) or sid in ids:
            probs.append(f"Quelle mit fehlender, ungültiger oder doppelter id: {sid!r}")
        ids.add(sid)
        if not s.get("name") or not s.get("url"):
            probs.append(f"{sid}: name oder url fehlt")
            continue
        for u in [s["url"]] + [e if isinstance(e, str) else e.get("url", "") for e in s.get("urls", []) or []]:
            if not str(u).startswith(("https://", "http://")) or is_placeholder(str(u)):
                probs.append(f"{sid}: ungültige oder Platzhalter-Adresse {str(u)[:60]}")
        for pat in s.get("ignoriere", []) or []:
            try:
                re.compile(pat)
            except re.error:
                probs.append(f"{sid}: ignoriere-Ausdruck ungültig: {pat[:40]}")
    try:
        tickets = load_tickets(root)
    except Exception as exc:
        raise Problem(f"tickets.yml nicht lesbar: {type(exc).__name__}")
    seen = set()
    for t in tickets:
        u = str(t.get("url", ""))
        if not u.startswith("https://") or is_placeholder(u) or u in seen:
            probs.append(f"Ticketseite ungültig, Platzhalter oder doppelt: {u[:60]}")
        seen.add(u)
    if probs:
        raise Problem("\n".join(probs))
    return f"OK: {len(sources)} Quellen, {len(tickets)} Ticketseiten"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="Ordner mit config/ (für Tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("ticket")
    t.add_argument("url")
    t.add_argument("--note", required=True)
    t.add_argument("--bis", required=True)
    t.add_argument("--js", action="store_true")
    q = sub.add_parser("quelle")
    q.add_argument("--name", required=True)
    q.add_argument("--url", required=True)
    q.add_argument("--group")
    q.add_argument("--note")
    q.add_argument("--harvest", action="store_true")
    q.add_argument("--js", action="store_true")
    sub.add_parser("aufraeumen")
    sub.add_parser("pruefe")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else ROOT
    try:
        print({"ticket": cmd_ticket, "quelle": cmd_quelle, "aufraeumen": cmd_aufraeumen, "pruefe": cmd_pruefe}[a.cmd](root, a))
    except Problem as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
