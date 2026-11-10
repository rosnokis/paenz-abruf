"""Tests with a fake fetcher. They run in a temporary folder and never touch config/ or out/ of
the repository, and never open a network connection.

Run:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scraper import details, main, pflege
from scraper.fetch import Response

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Berlin")
# Invented addresses on a real-looking domain: the scraper refuses reserved test domains on purpose.
LISTE = "https://buehne-attrappe.koeln/programm"
DETAIL = "https://buehne-attrappe.koeln/stueck/der-kleine-rabe"
TICKET = "https://buehne-attrappe.koeln/tickets/rabe"
SHOP = "https://kasse-attrappe.koeln/rabe"
KAPUTT = "https://kaputt-attrappe.koeln/"


def html(*lines: str) -> bytes:
    return ("<html><body><main>" + "".join(f"<p>{l}</p>" for l in lines) + "</main></body></html>").encode()


class FakeFetcher:
    def __init__(self, pages: dict[str, bytes]):
        self.pages, self.requests, self.asked = pages, 0, []

    def get(self, url, want="html"):
        self.requests += 1
        self.asked.append(url)
        if url in self.pages:
            return Response(url=url, final_url=url, status=200, content=self.pages[url], ctype="text/html")
        return Response(url=url, final_url=url, status=500, error="HTTP 500")

    render = get

    def get_api(self, url):
        return Response(url=url, final_url=url, error="kein Netz im Test")

    def close(self):
        pass


def args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(nur=None, commit_msg_file=None, ohne_wetter=True, ohne_extras=False, root=str(root))


class Ablauf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        self.write_config(
            [{"id": "buehne", "name": "Bühne", "url": LISTE}, {"id": "kaputt", "name": "Kaputt", "url": KAPUTT}],
            [{"url": TICKET, "bis": "2099-01-01"},
             {"url": "https://buehne-attrappe.koeln/alt", "bis": "2020-01-01"},
             {"url": "https://shop.example/voll"}])
        self.day = datetime(2026, 11, 10, 4, 30, tzinfo=TZ)

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, quellen, tickets):
        (self.root / "config" / "quellen.yml").write_text(yaml.safe_dump({"quellen": quellen}, allow_unicode=True))
        (self.root / "config" / "tickets.yml").write_text(yaml.safe_dump({"seiten": tickets}, allow_unicode=True))

    def run_day(self, pages, offset=0):
        f = FakeFetcher({"https://buehne-attrappe.koeln/robots.txt": b"", **pages})
        main.run(args(self.root), f, self.day + timedelta(days=offset))
        return f, (self.root / "out" / "heute.md").read_text()

    def test_neue_zeile_detailseite_und_rauschen(self):
        base = {LISTE: html("Kindertheater ab 3 Jahren", "1810 Veranstaltungen gefunden", "X/Twitter"),
                TICKET: html("Der kleine Rabe", "Tickets kaufen")}
        _, heute = self.run_day(base)
        self.assertIn("Erstabruf", heute)
        pages = dict(base)
        pages[LISTE] = html("Kindertheater ab 3 Jahren", "1811 Veranstaltungen gefunden", "X/Twitter",
                            f'<a href="{DETAIL}">Der kleine Rabe, Sonntag 15 Uhr</a>')
        pages[DETAIL] = html("Der kleine Rabe", "Sonntag 15.11.2026, 15 Uhr", "Eintritt 6 Euro")
        f, heute = self.run_day(pages, 1)
        self.assertIn("Der kleine Rabe, Sonntag 15 Uhr", heute)
        self.assertNotIn("Veranstaltungen gefunden", heute)
        self.assertNotIn("X/Twitter", heute)
        index = json.loads((self.root / "out" / "details" / "index.json").read_text())
        self.assertIn(DETAIL, index)
        self.assertIn("Eintritt 6 Euro", (self.root / index[DETAIL]["datei"]).read_text())
        self.assertFalse([u for u in f.asked if "example" in u], "Platzhalter-Adresse wurde abgerufen")

    def test_ticketstatus_folgeschritt_ablauf_platzhalter(self):
        pages = {LISTE: html("Programm"), TICKET: html("Der kleine Rabe", f'<a href="{SHOP}">Zum Vorverkauf</a>'),
                 SHOP: html("Der kleine Rabe", "Leider ausverkauft")}
        f, heute = self.run_day(pages)
        seiten = json.loads((self.root / "out" / "tickets.json").read_text())["seiten"]
        self.assertEqual(seiten[TICKET]["s"], "ausverkauft")
        self.assertEqual(seiten[TICKET]["ueber"], SHOP)
        self.assertEqual(seiten[TICKET]["art"], "ticketliste")
        self.assertEqual(list(seiten), [TICKET])
        self.assertNotIn("https://buehne-attrappe.koeln/alt", f.asked)
        self.assertIn("Abgelaufene Ticketseiten", heute)

    def test_teilweise(self):
        self.assertEqual(details.ticket_status(["Sa 14 Uhr ausverkauft", "So 11 Uhr Tickets kaufen"])[0], "teilweise")
        self.assertEqual(details.ticket_status(["Nur noch 3 Karten"])[0], "knapp")
        self.assertEqual(details.ticket_status(["Ein schönes Stück"])[0], "unklar")

    def test_dauerfehler_ab_dem_dritten_tag(self):
        pages = {LISTE: html("Programm"), TICKET: html("Tickets kaufen")}
        for offset in range(3):
            _, heute = self.run_day(pages, offset)
        self.assertIn("## Dauerfehler", heute)
        self.assertRegex(heute, r"`kaputt`: 3 Tage, seit 2026-11-10 – NEU")
        pages["https://kaputt-attrappe.koeln/robots.txt"] = b""
        pages[KAPUTT] = html("Wieder da")
        _, heute = self.run_day(pages, 3)
        self.assertNotIn("## Dauerfehler", heute)


class Pflege(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "quellen.yml").write_text(
            "# Kommentar bleibt\nquellen:\n- id: buehne\n  name: Bühne\n  url: https://buehne-attrappe.koeln/\n")
        (self.root / "config" / "tickets.yml").write_text(
            "seiten:\n- url: https://buehne-attrappe.koeln/alt\n  bis: 2020-01-01\n")
        self.bis = (pflege.today() + timedelta(days=20)).isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *argv):
        return pflege.main(["--root", str(self.root), *argv])

    def test_ticket(self):
        self.assertEqual(self.call("ticket", TICKET, "--note", "Rabe, So 15.11.", "--bis", self.bis), 0)
        self.assertEqual(self.call("ticket", "https://shop.example/x", "--note", "x", "--bis", self.bis), 1)
        self.assertEqual(self.call("ticket", "http://buehne-attrappe.koeln/x", "--note", "x", "--bis", self.bis), 1)
        self.assertEqual(self.call("ticket", TICKET + "2", "--note", "Mail an a@b.de", "--bis", self.bis), 1)
        self.assertEqual(self.call("ticket", TICKET + "3", "--note", "ok", "--bis", "2020-01-01"), 1)
        self.assertEqual(self.call("aufraeumen"), 0)
        self.assertEqual([t["url"] for t in pflege.load_tickets(self.root)], [TICKET])
        self.assertEqual(self.call("pruefe"), 0)

    def test_tageslimit(self):
        for i in range(pflege.MAX_TICKETS_TAG):
            self.assertEqual(self.call("ticket", f"{TICKET}/{i}", "--note", "n", "--bis", self.bis), 0)
        self.assertEqual(self.call("ticket", f"{TICKET}/zuviel", "--note", "n", "--bis", self.bis), 1)

    def test_quelle(self):
        self.assertEqual(self.call("quelle", "--name", "Neues Kindertheater Köln", "--url",
                                   "https://neues-theater-attrappe.koeln/spielplan", "--harvest"), 0)
        self.assertEqual(self.call("quelle", "--name", "Doppelt", "--url", "https://buehne-attrappe.koeln/x"), 0)
        text = (self.root / "config" / "quellen.yml").read_text()
        self.assertIn("# Kommentar bleibt", text)
        self.assertEqual([s["id"] for s in pflege.load_sources(self.root)], ["buehne", "neues-kindertheater-koeln"])
        self.assertEqual(self.call("pruefe"), 0)


class Repo(unittest.TestCase):
    """The real repository must be free of test leftovers."""

    def test_konfiguration_gueltig(self):
        self.assertEqual(pflege.main(["pruefe"]), 0)

    def test_keine_platzhalter_in_out(self):
        pat = re.compile(r"https?://[A-Za-z0-9.-]*(\.example\b|example\.(com|org|net)|attrappe\.koeln|localhost)")
        hits = [str(p.relative_to(ROOT)) for p in (ROOT / "out").rglob("*")
                if p.is_file() and p.suffix in (".md", ".json") and pat.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
