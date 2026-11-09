"""Nightly fetch of all sources -> out/ (texts, events, status, daily summary, weather).

Run:  python -m scraper.main [--nur ID[,ID]] [--commit-msg-file PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from zoneinfo import ZoneInfo

import yaml

from . import extract, wetter
from .fetch import Fetcher

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "quellen.yml"
OUT = ROOT / "out"
QDIR = OUT / "quellen"
TZ = ZoneInfo("Europe/Berlin")
SECTION = re.compile(r"^## Quelle: (\S+)\s*$")
HEUTE_BUDGET = 26_000  # bytes for the "changed" part of heute.md
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]
WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ======================================================================= helpers
def now_local() -> datetime:
    return datetime.now(TZ).replace(second=0, microsecond=0)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def with_param(url: str, key: str, value) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, str(value))]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def parse_sections(md: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    cur = None
    for line in md.splitlines():
        m = SECTION.match(line)
        if m:
            cur = m.group(1)
            sections[cur] = []
        elif cur is not None and line.strip():
            sections[cur].append(line)
    return sections


def body_lines(md: str) -> set[str]:
    return {l for lines in parse_sections(md).values() for l in lines}


# ======================================================================= per URL
class Section:
    def __init__(self, url: str):
        self.url = url
        self.lines: list[str] = []
        self.events: list[dict] = []
        self.status: int | None = None
        self.bytes = 0
        self.error: str | None = None
        self.blocked = False
        self.empty = False  # e.g. weekly PDF not (yet) published -> not an error
        self.hints: list[str] = []
        self.pdf_links: list[str] = []

    def info(self) -> dict:
        d = {"url": self.url, "http_status": self.status, "bytes": self.bytes, "zeilen": len(self.lines),
             "termine": len(self.events)}
        if self.error:
            d["fehler"] = self.error
        if self.blocked:
            d["blocked"] = True
        if self.empty:
            d["leer"] = True
        if self.hints:
            d["hinweise"] = self.hints
        return d


def compile_ignore(src: dict) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in src.get("ignoriere", []) or []]


def fetch_one(f: Fetcher, url: str, opts: dict, ignore) -> Section:
    sec = Section(url)
    typ = opts.get("typ", "html")
    if opts.get("js"):
        r = f.render(url, opts.get("js_klick"), int(opts.get("js_klick_max", 3)))
    else:
        r = f.get(url, "pdf" if typ == "pdf" else "html")
    sec.status, sec.bytes = r.status, len(r.content)
    if not r.ok:
        sec.error, sec.blocked = r.error, r.blocked
        return sec
    try:
        if r.ctype in ("application/pdf", "application/x-pdf"):
            sec.lines, h = extract.pdf_to_lines(r.content, ignore)
            sec.hints += h
        else:
            sec.lines, sec.events, h = extract.html_to_lines(r.content, r.final_url, opts.get("selector"), ignore)
            sec.hints += h
        if opts.get("ab") or opts.get("bis"):
            sec.lines, h = extract.cut(sec.lines, opts.get("ab"), opts.get("bis"))
            sec.hints += h
        if opts.get("folge_pdf") and r.ctype.startswith("text/html"):
            pat = re.compile(opts["folge_pdf"], re.IGNORECASE)
            sec.pdf_links = [u for u in extract.links_matching(r.content, r.final_url, pat)
                             if u.lower().split("?")[0].endswith(".pdf")][:2]
    except Exception as exc:  # parser trouble on hostile/broken input: report, don't crash
        sec.error = f"Parserfehler: {type(exc).__name__}"
    if not sec.error and not sec.lines and not sec.events:
        sec.error = "kein Text gefunden (evtl. JavaScript-Seite -> js: true)"
    return sec


def fetch_paged(f: Fetcher, url: str, opts: dict, ignore) -> Section:
    pg = opts["paginate"]
    param, step, maxp = pg.get("param", "start"), int(pg.get("step", 20)), int(pg.get("max_seiten", 10))
    first = fetch_one(f, url, opts, ignore)
    if first.error:
        return first
    seen = set(first.lines)
    for i in range(1, maxp):
        sec = fetch_one(f, with_param(url, param, i * step), opts, ignore)
        new = [l for l in sec.lines if l not in seen] if not sec.error else []
        if not new:
            break
        first.lines += new
        seen.update(new)
        first.events += [e for e in sec.events if e not in first.events]
        first.bytes += sec.bytes
    else:
        first.hints.append(f"Seitenlimit {maxp} erreicht")
    return first


def nippes_candidates(f: Fetcher, src: dict, today: date) -> list[str]:
    def pdf_url(monday: date) -> str:
        return (f"https://bilderstoeckchen.sozialraumkoordination.koeln/downloads/{monday.year}/"
                f"Freizeitkalender_{monday.day:02d}_{MONATE[monday.month - 1]}_{monday.year}.pdf")

    monday = today - timedelta(days=today.weekday())
    urls = [pdf_url(monday), pdf_url(monday + timedelta(days=7))]
    # Also look at links on the homepage (editions sometimes cover several weeks).
    r = f.get(src["url"], "html")
    if r.ok:
        pat = re.compile(r"Freizeitkalender_(\d{1,2})_([A-Za-zä]+)_(\d{4})\.pdf$", re.IGNORECASE)
        for u in extract.links_matching(r.content, r.final_url, pat):
            m = pat.search(u)
            try:
                d = date(int(m.group(3)), MONATE.index(m.group(2).capitalize()) + 1, int(m.group(1)))
            except ValueError:
                continue
            if d >= monday - timedelta(days=21) and u not in urls:
                urls.append(u)
    return urls


# ======================================================================= per source
def process(f: Fetcher, src: dict, today: date, old_md: str) -> tuple[list[Section], str | None]:
    """Return sections (in order) and an optional source-level hint."""
    ignore = compile_ignore(src)
    base_opts = {k: src[k] for k in ("selector", "js", "js_klick", "js_klick_max", "typ", "ab", "bis",
                                     "folge_pdf") if k in src}
    per_url = ("selector", "ab", "bis", "folge_pdf")
    hint = None

    if src.get("typ") == "nippes_pdf":
        secs = []
        for u in nippes_candidates(f, src, today):
            s = fetch_one(f, u, {"typ": "pdf"}, ignore)
            if s.status == 404:
                s.error, s.empty = None, True
            secs.append(s)
        if all(s.empty for s in secs):
            hint = "keine aktuelle Ausgabe gefunden (geprüft: " + ", ".join(
                urlsplit(s.url).path.rsplit("/", 1)[-1] for s in secs) + ")"
        return secs, hint

    entries: list[tuple[str, dict]] = [(src["url"], base_opts)]
    for e in src.get("urls", []) or []:
        if isinstance(e, str):
            entries.append((e, {k: v for k, v in base_opts.items() if k not in per_url}))
        else:
            opts = {k: v for k, v in base_opts.items() if k not in per_url}
            opts.update({k: v for k, v in e.items() if k != "url"})
            entries.append((e["url"], opts))

    secs = []
    for url, opts in entries:
        s = fetch_paged(f, url, opts, ignore) if opts.get("paginate") else fetch_one(f, url, opts, ignore)
        secs.append(s)
        for pdf in s.pdf_links:
            if pdf not in [x.url for x in secs]:
                secs.append(fetch_one(f, pdf, {"typ": "pdf"}, ignore))
    return secs, hint


def render_md(src: dict, sections: list[tuple[str, list[str]]]) -> str:
    out = [f"# {src['name']} ({src['id']})", ""]
    for url, lines in sections:
        out.append(f"## Quelle: {url}")
        out.extend(lines)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ======================================================================= heute.md
def fmt_date(s: str | None) -> str:
    if not s:
        return "?"
    try:
        if len(s) <= 10:
            d = date.fromisoformat(s)
            return f"{WOCHENTAG[d.weekday()]} {d.day:02d}.{d.month:02d}.{d.year}"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(TZ)
        return f"{WOCHENTAG[dt.weekday()]} {dt.day:02d}.{dt.month:02d}.{dt.year} {dt:%H:%M}"
    except ValueError:
        return s


def fmt_event(e: dict) -> str:
    when = fmt_date(e["startDate"]) if e.get("startDate") else (
        "bis " + fmt_date(e["endDate"][:10]) if e.get("endDate") else "?")
    parts = [when, e["name"]]
    loc = e.get("location") or {}
    if loc:
        parts.append(", ".join(x for x in [loc.get("name"), loc.get("address")] if x))
    for o in (e.get("offers") or [])[:1]:
        if o.get("price") not in (None, ""):
            parts.append(f"{o.get('price')} {o.get('priceCurrency', '')}".strip())
    for k in ("typicalAgeRange", "audience"):
        if e.get(k):
            parts.append(e[k])
    if e.get("eventStatus") and e["eventStatus"] != "EventScheduled":
        parts.append(e["eventStatus"])
    line = " – ".join(p for p in parts if p)
    if e.get("url"):
        line += f" – [Details]({e['url']})"
    return ("- " + extract.MARK if e.get("kinder") else "- ") + line


def build_heute(stand: datetime, results: list[dict], wetter_err: str | None, sources: dict) -> str:
    cnt = {k: sum(1 for r in results if r["status"] == k) for k in ("ok", "teilweise", "fehler", "blocked", "skip")}
    changed = [r for r in results if r.get("geaendert") and r["status"] in ("ok", "teilweise")]
    lines = [
        f"STAND: {stand:%Y-%m-%dT%H:%M}",
        f"# Pänz-Quellen – Abruf {stand:%d.%m.%Y %H:%M}",
        "",
        f"Quellen: {len(results)} | ok: {cnt['ok'] + cnt['teilweise']} | geändert: {len(changed)} | "
        f"Fehler: {cnt['fehler'] + cnt['teilweise']} | blockiert: {cnt['blocked']} | übersprungen: {cnt['skip']}",
        f"Wetter: {'ok (out/wetter.json)' if not wetter_err else 'FEHLER: ' + wetter_err + ' (alter Stand bleibt)'}",
        f"Legende: {extract.MARK.strip()} = Kleinkind-Stichwort gefunden (nur Markierung, nichts gefiltert). "
        "Volltext je Quelle: out/quellen/<id>.md, Termine: out/quellen/<id>.events.json",
        "",
    ]
    if changed:
        lines.append("## Geändert (nur neue Zeilen/Termine gegenüber dem letzten erfolgreichen Abruf)")
        per = max(1200, HEUTE_BUDGET // max(len(changed), 1))
        for r in changed:
            block = [f"### {sources[r['id']]['name']} (`{r['id']}`)"]
            if r.get("erstabruf"):
                block.append(f"Erstabruf: {r['zeilen']} Zeilen, {r['termine']} Termine (kein Vergleich möglich).")
                lines += block + [""]
                continue
            new_ev, new_lines = r["neue_termine"], r["neue_zeilen"]
            if r.get("hinweis"):
                block.append(f"_Hinweis: {r['hinweis']}_")
            body: list[str] = []
            if new_ev:
                body.append(f"Neue Termine ({len(new_ev)}):")
                body += [fmt_event(e) for e in sorted(new_ev, key=lambda e: e.get("startDate") or e.get("endDate") or "")]
            if new_lines:
                marked = [l for l in new_lines if l.startswith(extract.MARK)]
                rest = [l for l in new_lines if not l.startswith(extract.MARK)]
                body.append(f"Neue Zeilen ({len(new_lines)}, davon {len(marked)} {extract.MARK.strip()}):")
                body += ["  " + l for l in marked + rest]
            if not new_ev and not new_lines:
                body.append("Nur Zeilen entfallen/umsortiert, nichts Neues.")
            size, shown = 0, []
            for i, l in enumerate(body):
                size += len(l.encode("utf-8")) + 1
                if size > per and i > 1:
                    rem = body[i:]
                    rem_m = sum(1 for x in rem if extract.MARK in x[:6])
                    shown.append(f"  … +{len(rem)} weitere (davon {rem_m} {extract.MARK.strip()}), "
                                 f"siehe out/quellen/{r['id']}.md")
                    break
                shown.append(l)
            lines += block + shown + [""]
    unchanged = [r["id"] for r in results if r["status"] in ("ok", "teilweise") and not r.get("geaendert")]
    if unchanged:
        lines += ["## Unverändert", ", ".join(unchanged), ""]
    probs = [r for r in results if r["status"] in ("fehler", "blocked", "teilweise", "skip")]
    hints = [r for r in results if r.get("hinweis") and r["status"] == "ok" and not r.get("geaendert")]
    if probs or hints:
        lines.append("## Fehler, Sperren, Hinweise")
        for r in probs:
            lines.append(f"- `{r['id']}`: {r['status']} – {r.get('fehler') or r.get('hinweis') or ''}".rstrip(" –"))
        for r in hints:
            lines.append(f"- `{r['id']}`: Hinweis – {r['hinweis']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ======================================================================= main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nur", help="nur diese IDs (kommagetrennt), zum Testen")
    ap.add_argument("--commit-msg-file")
    ap.add_argument("--ohne-wetter", action="store_true")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in cfg["quellen"]}
    ids = [i for i in sources if not args.nur or i in args.nur.split(",")]
    QDIR.mkdir(parents=True, exist_ok=True)
    old_status = {r["id"]: r for r in read_json(OUT / "status.json", {}).get("quellen", [])}

    stand = now_local()
    today = stand.date()
    f = Fetcher()
    results = []
    try:
        for sid in ids:
            src = sources[sid]
            prev = old_status.get(sid, {})
            r = {"id": sid, "url": src["url"], "abgerufen_um": f"{now_local():%Y-%m-%dT%H:%M}",
                 "letzter_erfolg": prev.get("letzter_erfolg"), "sha256": prev.get("sha256"),
                 "geaendert": False, "fehler": None}
            if src.get("skip"):
                r.update(status="skip", hinweis=str(src["skip"]), http_status=None, bytes=0)
                results.append(r)
                print(f"[skip]    {sid}")
                continue
            md_path, ev_path = QDIR / f"{sid}.md", QDIR / f"{sid}.events.json"
            old_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            old_sections = parse_sections(old_md)
            secs, hint = process(f, src, today, old_md)

            good = [s for s in secs if not s.error and not s.empty]
            bad = [s for s in secs if s.error]
            r["urls"] = [s.info() for s in secs]
            r["http_status"] = secs[0].status if secs else None
            r["bytes"] = sum(s.bytes for s in secs)
            if hint:
                r["hinweis"] = hint
            if bad:
                r["fehler"] = "; ".join(f"{s.url}: {s.error}" for s in bad)

            if not good:
                if bad:
                    r["status"] = "blocked" if all(s.blocked for s in bad) else "fehler"
                else:  # only empty sections (weekly PDF missing): keep last good state
                    r["status"] = "ok"
                results.append(r)
                print(f"[{r['status']:8}] {sid}: {r.get('fehler') or hint}")
                continue

            # Build new text: good sections fresh, failed ones from the last good state.
            out_sections = []
            for s in secs:
                if s.empty:
                    continue
                if s.error:
                    if s.url in old_sections:
                        out_sections.append((s.url, old_sections[s.url]))
                    continue
                out_sections.append((s.url, s.lines))
            md = render_md(src, out_sections)
            # Events: fresh from good sections; for failed sections keep the last good ones.
            old_events = read_json(ev_path, [])
            failed_urls = {s.url for s in bad}
            events, seen_keys = [], set()
            candidates = [dict(e, quelle=s.url) for s in good for e in s.events]
            candidates += [e for e in old_events if e.get("quelle") in failed_urls]
            for e in candidates:
                k = extract.event_key(e)
                if k not in seen_keys:
                    seen_keys.add(k)
                    events.append(e)
            events.sort(key=lambda e: (e.get("startDate") or e.get("endDate") or "", e["name"]))

            new_sha = sha(md + json.dumps(events, ensure_ascii=False, sort_keys=True))
            r["status"] = "teilweise" if bad else "ok"
            r["zeilen"] = sum(len(l) for _, l in out_sections)
            r["termine"] = len(events)
            r["geaendert"] = new_sha != prev.get("sha256")
            r["erstabruf"] = not prev.get("sha256") or not old_md
            if r["geaendert"]:
                old_lines = body_lines(old_md)
                r["neue_zeilen"] = [l for _, ls in out_sections for l in ls if l not in old_lines]
                old_keys = {extract.event_key(e) for e in old_events}
                r["neue_termine"] = [e for e in events if extract.event_key(e) not in old_keys]
                md_path.write_text(md, encoding="utf-8")
                if events:
                    write_json(ev_path, events)
                elif ev_path.exists():
                    ev_path.unlink()
            r["sha256"] = new_sha
            r["letzter_erfolg"] = r["abgerufen_um"]
            results.append(r)
            print(f"[{r['status']:8}] {sid}: {r['zeilen']} Zeilen, {r['termine']} Termine"
                  f"{', geändert' if r['geaendert'] else ''}{' | ' + r['fehler'] if r['fehler'] else ''}")

        wetter_err = None
        if not args.ohne_wetter:
            w, wetter_err = wetter.fetch(f, today)
            if w:
                write_json(OUT / "wetter.json", w)
            print(f"[wetter]  {'ok' if w else wetter_err}")
    finally:
        f.close()

    # keep entries of sources not processed in this run (--nur)
    done = {r["id"] for r in results}
    keep = [v for k, v in old_status.items() if k not in done and k in sources]
    heute = build_heute(stand, results, wetter_err, sources)
    (OUT / "heute.md").write_text(heute, encoding="utf-8")
    status_rows = []
    for r in results + keep:
        status_rows.append({k: v for k, v in r.items() if k not in ("neue_zeilen", "neue_termine", "erstabruf")})
    status_rows.sort(key=lambda r: list(sources).index(r["id"]))
    write_json(OUT / "status.json", {"stand": f"{stand:%Y-%m-%dT%H:%M}", "anfragen": f.requests,
                                     "wetter_fehler": wetter_err, "quellen": status_rows})

    n_changed = sum(1 for r in results if r.get("geaendert"))
    n_err = sum(1 for r in results if r["status"] in ("fehler", "blocked", "teilweise"))
    msg = f"Abruf {today.isoformat()}: {n_changed} geändert, {n_err} Fehler"
    print(msg, f"| heute.md {len(heute.encode('utf-8'))} Bytes | {f.requests} Anfragen")
    if args.commit_msg_file:
        Path(args.commit_msg_file).write_text(msg + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
