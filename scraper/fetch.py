"""HTTP fetching: polite, size and time limited, honouring robots.txt.

Everything fetched here is untrusted data. It is only returned as bytes or
text and parsed later. Nothing is ever executed (the optional Playwright
renderer runs the page's JavaScript only inside the sandboxed browser).
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

USER_AGENT = "PaenzKalenderBot/1.0 (privater Familienkalender; Kontakt über GitHub rosnokis)"
UA_TOKEN = "PaenzKalenderBot"
TIMEOUT = 30            # seconds per request
MAX_BYTES = 5 * 1024 * 1024
DELAY = 1.5             # seconds between requests
MAX_CRAWL_DELAY = 10    # cap for a robots.txt Crawl-delay

HTML_TYPES = ("text/html", "application/xhtml+xml")
PDF_TYPES = ("application/pdf", "application/x-pdf")
BLOCK_CODES = {401, 403, 429, 451}
CHALLENGE_MARKERS = (b"cf-chl", b"challenge-platform", b"Just a moment...", b"captcha-delivery",
                     b"Attention Required! | Cloudflare", b"_Incapsula_Resource")


@dataclass
class Response:
    url: str
    final_url: str = ""
    status: int | None = None
    content: bytes = b""
    ctype: str = ""
    error: str | None = None
    blocked: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class Fetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        })
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str | None]] = {}
        self._last: dict[str, float] = {}
        self._pw = None
        self._browser = None
        self.requests = 0

    # ------------------------------------------------------------------ helpers
    def _wait(self, host: str, extra: float = 0.0) -> None:
        delay = max(DELAY, min(extra, MAX_CRAWL_DELAY))
        last = max(self._last.values(), default=0.0)
        host_last = self._last.get(host, 0.0)
        now = time.monotonic()
        wait = max(last + DELAY - now, host_last + delay - now, 0)
        if wait:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _raw_get(self, url: str, accept: tuple[str, ...]) -> Response:
        res = self._raw_get_once(url, accept)
        if res.error and res.error.startswith(("Netzwerkfehler", "Timeout")) and not res.status:
            time.sleep(5)
            res = self._raw_get_once(url, accept)
            res.notes.append("zweiter Versuch")
        return res

    def _raw_get_once(self, url: str, accept: tuple[str, ...]) -> Response:
        res = Response(url=url, final_url=url)
        host = urlsplit(url).netloc
        rp = self._robots.get(_origin(url), (None, None))[0]
        crawl_delay = 0.0
        if rp is not None:
            try:
                crawl_delay = float(rp.crawl_delay(UA_TOKEN) or 0)
            except (TypeError, ValueError):
                crawl_delay = 0.0
        self._wait(host, crawl_delay)
        self.requests += 1
        try:
            with self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
                res.status = r.status_code
                res.final_url = r.url
                res.ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                length = r.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_BYTES:
                    res.error = f"zu groß ({int(length)} Bytes)"
                    return res
                buf = bytearray()
                started = time.monotonic()
                for chunk in r.iter_content(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        res.error = f"zu groß (> {MAX_BYTES} Bytes)"
                        return res
                    if time.monotonic() - started > TIMEOUT:
                        res.error = "Timeout beim Lesen"
                        return res
                res.content = bytes(buf)
        except requests.exceptions.Timeout:
            res.error = "Timeout"
            return res
        except requests.exceptions.RequestException as exc:
            res.error = f"Netzwerkfehler: {type(exc).__name__}"
            return res

        if res.status in BLOCK_CODES or (res.status and res.status >= 400 and _is_challenge(res.content)):
            res.blocked = True
            res.error = f"gesperrt (HTTP {res.status})"
            return res
        if res.status and res.status >= 400:
            res.error = f"HTTP {res.status}"
            return res
        if _is_challenge(res.content[:20000]) and len(res.content) < 60000:
            res.blocked = True
            res.error = "gesperrt (Bot-Schutz/Challenge-Seite)"
            return res
        if accept and not any(res.ctype.startswith(a) for a in accept):
            # Some servers send PDFs as octet-stream; accept by magic bytes.
            if "pdf" in accept and res.content[:5] == b"%PDF-":
                res.ctype = "application/pdf"
            elif any(a in HTML_TYPES for a in accept) and not res.ctype and res.content.lstrip()[:1] == b"<":
                res.ctype = "text/html"
            else:
                res.error = f"unerwarteter Inhaltstyp: {res.ctype or 'unbekannt'}"
        return res

    # ------------------------------------------------------------------ robots
    def robots_allowed(self, url: str) -> tuple[bool, str | None]:
        origin = _origin(url)
        if origin not in self._robots:
            r = self._raw_get(origin + "/robots.txt", ())
            if r.ok and r.status == 200:
                rp = urllib.robotparser.RobotFileParser()
                text = r.content.decode("utf-8", errors="replace")
                if text.lstrip().startswith("<"):
                    # HTML instead of robots.txt -> treat as "no rules"
                    rp.parse([])
                else:
                    rp.parse(text.splitlines())
                self._robots[origin] = (rp, None)
            elif r.status is not None and 400 <= r.status < 500:
                # RFC 9309: robots.txt "unavailable" -> no restrictions
                rp = urllib.robotparser.RobotFileParser()
                rp.parse([])
                self._robots[origin] = (rp, None)
            else:
                # RFC 9309: "unreachable" -> assume complete disallow
                self._robots[origin] = (None, f"robots.txt nicht erreichbar ({r.error or r.status})")
        rp, why = self._robots[origin]
        if rp is None:
            return False, why
        if not rp.can_fetch(UA_TOKEN, url):
            return False, "robots.txt verbietet den Abruf"
        return True, None

    # ------------------------------------------------------------------ public
    def get(self, url: str, want: str = "html") -> Response:
        """want: 'html' or 'pdf'."""
        allowed, why = self.robots_allowed(url)
        if not allowed:
            return Response(url=url, final_url=url, error=why, blocked=not why.startswith("robots.txt nicht"))
        accept = PDF_TYPES if want == "pdf" else HTML_TYPES
        return self._raw_get(url, accept)

    def get_api(self, url: str) -> Response:
        """For the weather API (JSON, no robots.txt check, same UA and limits)."""
        return self._raw_get(url, ("application/json",))

    def render(self, url: str, click: str | None = None, clicks: int = 0) -> Response:
        """Render a JavaScript page with Playwright (only for sources marked js: true)."""
        allowed, why = self.robots_allowed(url)
        if not allowed:
            return Response(url=url, final_url=url, error=why, blocked=True)
        res = Response(url=url, final_url=url)
        try:
            from playwright.sync_api import sync_playwright  # imported lazily
        except ImportError:
            res.error = "Playwright nicht installiert"
            return res
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        self._wait(urlsplit(url).netloc)
        self.requests += 1
        ctx = self._browser.new_context(user_agent=USER_AGENT, locale="de-DE",
                                        accept_downloads=False, java_script_enabled=True)
        page = ctx.new_page()
        page.route("**/*", lambda route: route.abort()
                   if route.request.resource_type in ("image", "media", "font") else route.continue_())
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
            res.status = resp.status if resp else None
            res.final_url = page.url
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            for _ in range(clicks if click else 0):
                loc = page.locator(click).first
                if loc.count() == 0 or not loc.is_visible():
                    break
                loc.click(timeout=5000)
                time.sleep(DELAY)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            html = page.content().encode("utf-8")
            if len(html) > MAX_BYTES:
                res.error = "zu groß"
            else:
                res.content = html
                res.ctype = "text/html"
            if res.status in BLOCK_CODES:
                res.blocked = True
                res.error = f"gesperrt (HTTP {res.status})"
            elif res.status and res.status >= 400:
                res.error = f"HTTP {res.status}"
        except Exception as exc:  # playwright raises many kinds
            res.error = f"Browserfehler: {type(exc).__name__}"
        finally:
            ctx.close()
        return res

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _is_challenge(content: bytes) -> bool:
    return any(m in content for m in CHALLENGE_MARKERS)
