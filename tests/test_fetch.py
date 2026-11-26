"""Fetcher: no requests to internal addresses (SSRF), also not via redirects.

Runs offline with a local test server. 127.0.0.1 stands in for a public host,
"localhost" for an internal one (the real check is replaced only for that).
"""
import http.server
import socket
import threading
import time
import unittest
from unittest import mock

from scraper import fetch


class AdresseTest(unittest.TestCase):
    def check(self, url, addrs):
        return fetch.address_problem(url, resolve=lambda h, p: addrs)

    def test_oeffentlich_erlaubt(self):
        self.assertIsNone(self.check("https://www.koeln.de/x", ["93.184.216.34", "2606:2800:220:1::1"]))

    def test_intern_gesperrt(self):
        for a in ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.1", "169.254.169.254",
                  "100.64.0.1", "0.0.0.0", "::1", "fe80::1%eth0", "fd00::1", "::ffff:10.0.0.1"]:
            self.assertIsNotNone(self.check("https://x.de/", [a]), a)

    def test_eine_interne_adresse_reicht(self):
        self.assertIsNotNone(self.check("https://x.de/", ["93.184.216.34", "10.0.0.1"]))

    def test_nur_http(self):
        for u in ["file:///etc/passwd", "ftp://x.de/", "gopher://x.de/"]:
            self.assertIsNotNone(fetch.address_problem(u, resolve=lambda h, p: ["93.184.216.34"]), u)


class DnsFehlerTest(unittest.TestCase):
    def test_dns_fehler_sperrt_und_wird_nicht_gemerkt(self):
        antworten = [socket.gaierror("SERVFAIL"), ["127.0.0.1"]]

        def flaky(host, port):
            a = antworten.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        with mock.patch.object(fetch, "_resolve", flaky):
            f = fetch.Fetcher()
            erst = f._address_problem("http://boese-attrappe.koeln/a")
            dann = f._address_problem("http://boese-attrappe.koeln/b")
        self.assertTrue(erst and erst.startswith("Netzwerkfehler"), erst)
        self.assertIn("interne Adresse", dann or "")

    def test_leere_dns_antwort_sperrt(self):
        self.assertIsNotNone(fetch.address_problem("https://x.de/", resolve=lambda h, p: []))


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: dict = {}

    def do_GET(self):
        port = self.server.server_address[1]
        path = self.path.split("?")[0]
        _Handler.hits[(self.headers.get("Host", "").split(":")[0], path)] = \
            _Handler.hits.get((self.headers.get("Host", "").split(":")[0], path), 0) + 1
        if path == "/nach-intern":
            self.send_response(302); self.send_header("Location", f"http://localhost:{port}/geheim"); self.end_headers()
        elif path == "/nach-oeffentlich":
            self.send_response(301); self.send_header("Location", "/ziel"); self.end_headers()
        elif path == "/schleife":
            self.send_response(302); self.send_header("Location", "/schleife"); self.end_headers()
        elif path == "/ws-seite":
            body = (b'<html><body><main><p>Puppentheater</p>'
                    b'<script>try { new WebSocket("ws://" + location.host + "/ws-test"); } catch (e) {}</script>'
                    b'</main></body></html>')
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        elif path == "/ws-test":
            self.send_response(404); self.end_headers()
        elif path == "/seite":
            body = (f'<html><body><main><p>Kinderkonzert am Sonntag</p>'
                    f'<iframe src="http://localhost:{port}/geheim"></iframe>'
                    f'<script>fetch("http://localhost:{port}/geheim").catch(()=>{{}});'
                    f'fetch("/nach-intern").catch(()=>{{}});</script></main></body></html>').encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        elif path in ("/ziel", "/geheim"):
            body = b"<html><body><main><p>Inhalt " + path.encode() + b"</p></main></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def _fake_problem(url, resolve=None):
    host = fetch.urlsplit(url).hostname
    if fetch.urlsplit(url).scheme not in ("http", "https"):
        return "nur http/https"
    return "interne Adresse (Test)" if host == "localhost" else None


class WeiterleitungTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Handler.hits.clear()
        p1 = mock.patch.object(fetch, "address_problem", _fake_problem)
        p2 = mock.patch.object(fetch, "DELAY", 0)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.f = fetch.Fetcher()
        self.addCleanup(self.f.close)

    def test_weiterleitung_nach_intern_gesperrt(self):
        r = self.f.get(self.base + "/nach-intern")
        self.assertIn("Weiterleitung gesperrt", r.error or "")
        self.assertNotIn(("localhost", "/geheim"), _Handler.hits)

    def test_weiterleitung_nach_oeffentlich_erlaubt(self):
        r = self.f.get(self.base + "/nach-oeffentlich")
        self.assertIsNone(r.error)
        self.assertIn(b"/ziel", r.content)
        self.assertTrue(r.final_url.endswith("/ziel"))

    def test_zu_viele_weiterleitungen(self):
        r = self.f.get(self.base + "/schleife")
        self.assertEqual(r.error, "zu viele Weiterleitungen")

    def test_interne_adresse_direkt(self):
        r = self.f.get(f"http://localhost:{self.srv.server_address[1]}/geheim")
        self.assertIn("gesperrt", r.error or "")
        self.assertEqual(_Handler.hits, {})

    def _browser_oder_skip(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright nicht installiert")
        try:
            with sync_playwright() as p:
                p.chromium.launch().close()
        except Exception as exc:
            self.skipTest(f"Browser startet nicht: {type(exc).__name__}")

    def test_browser_oeffnet_keine_websockets(self):
        self._browser_oder_skip()
        time.sleep(0)
        r = self.f.render(self.base + "/ws-seite")
        self.assertIsNone(r.error)
        self.assertIn(b"Puppentheater", r.content)
        time.sleep(1)
        self.assertNotIn(("127.0.0.1", "/ws-test"), _Handler.hits)

    def test_browser_laedt_nichts_internes(self):
        self._browser_oder_skip()
        r = self.f.render(self.base + "/seite")
        self.assertIsNone(r.error)
        self.assertIn(b"Kinderkonzert", r.content)
        self.assertNotIn(("localhost", "/geheim"), _Handler.hits)
        r = self.f.render(self.base + "/nach-intern")
        self.assertIn("Weiterleitung gesperrt", r.error or "")
        self.assertNotIn(("localhost", "/geheim"), _Handler.hits)
        r = self.f.render(self.base + "/nach-oeffentlich")
        self.assertIsNone(r.error)
        self.assertIn(b"/ziel", r.content)
        self.assertTrue(r.final_url.endswith("/ziel"))
        r = self.f.render(self.base + "/schleife")
        self.assertEqual(r.error, "zu viele Weiterleitungen")


if __name__ == "__main__":
    unittest.main()
