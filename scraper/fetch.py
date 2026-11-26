"""HTTP fetching: polite, size and time limited, honouring robots.txt.

Everything fetched here is untrusted data. It is only returned as bytes or
text and parsed later. Nothing is ever executed (the optional Playwright
renderer runs the page's JavaScript only inside the sandboxed browser).
"""
from __future__ import annotations

import ipaddress
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

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
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
CHALLENGE_MARKERS = (b"cf-chl", b"challenge-platform", b"Just a moment...", b"captcha-delivery",
                     b"Attention Required! | Cloudflare", b"_Incapsula_Resource")


# Public addresses that still reach the runner's host: the Azure WireServer on GitHub runners and
# NAT64 prefixes, which can map to internal IPv4 addresses.
BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in ("168.63.129.16/32", "64:ff9b::/96", "64:ff9b:1::/48"))


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def address_problem(url: str, resolve=None) -> str | None:
    """None if the URL may be fetched, else the reason.

    Only http(s) and only hosts whose every address is public. This blocks requests to the runner
    itself, to private networks and to cloud metadata services (SSRF), also after a redirect.
    """
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        return f"nur http/https erlaubt ({p.scheme or 'ohne Schema'})"
    host = p.hostname
    if not host:
        return "Adresse ohne Host"
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return "ungültiger Port"
    try:
        addrs = (resolve or _resolve)(host, port)
    except (OSError, UnicodeError):
        addrs = []
    if not addrs:
        # No request without a checked address. "Netzwerkfehler" keeps the usual retry; the
        # Fetcher does not cache this result, so the next attempt resolves and checks again.
        return "Netzwerkfehler: DNS (Name nicht auflösbar)"
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            return f"unbekannte Adresse ({a})"
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or any(ip in n for n in BLOCKED_NETS if n.version == ip.version):
            return f"interne Adresse nicht erlaubt ({host} -> {ip})"
    return None


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
        self._host_ok: dict[str, str | None] = {}

    def _address_problem(self, url: str) -> str | None:
        p = urlsplit(url)
        key = f"{p.scheme}://{p.netloc}"
        if key in self._host_ok:
            return self._host_ok[key]
        problem = address_problem(url)
        if not (problem or "").startswith("Netzwerkfehler"):
            self._host_ok[key] = problem  # never cache a DNS failure as a result
        return problem

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
        problem = self._address_problem(url)
        if problem:
            res.error = problem if problem.startswith("Netzwerkfehler") else f"gesperrt: {problem}"
            return res
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
            current = url
            for hop in range(MAX_REDIRECTS + 1):
                r = self.session.get(current, timeout=TIMEOUT, stream=True, allow_redirects=False)
                location = r.headers.get("Location")
                if r.status_code not in REDIRECT_CODES or not location:
                    break
                r.close()
                current = urljoin(current, location)
                problem = self._address_problem(current)
                if problem:
                    res.status, res.final_url = r.status_code, current
                    res.error = f"Weiterleitung gesperrt: {problem}"
                    return res
                if hop == MAX_REDIRECTS:
                    res.error = "zu viele Weiterleitungen"
                    return res
                self._wait(urlsplit(current).netloc)
            with r:
                res.status = r.status_code
                res.final_url = current
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
        problem = self._address_problem(url)
        if problem:
            return Response(url=url, final_url=url, error=f"gesperrt: {problem}")
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
        # Service workers and WebSockets would bypass the route and could reach internal
        # addresses. Event pages need neither, so both are blocked. Routes sit on the context,
        # so they also cover popups (window.open, target=_blank).
        ctx = self._browser.new_context(user_agent=USER_AGENT, locale="de-DE", accept_downloads=False,
                                        java_script_enabled=True, service_workers="block")
        # A WebSocket route that never calls connect_to_server() opens no connection.
        ctx.route_web_socket("**/*", lambda ws: None)
        page = ctx.new_page()
        redirect: dict[str, str | None] = {"ziel": None}

        def _route(route):
            req = route.request
            try:
                fremd = req.frame.page != page
            except Exception:  # navigation of a new popup: its frame does not exist yet
                fremd = req.is_navigation_request()
            if fremd:
                route.abort()  # popups are not needed for the text
                return
            if req.resource_type in ("image", "media", "font") or self._address_problem(req.url):
                route.abort()  # no media; no requests to internal addresses
                return
            # Load without following redirects, so every hop passes this check.
            try:
                resp = route.fetch(max_redirects=0, timeout=TIMEOUT * 1000)
            except Exception:
                route.abort()
                return
            if 300 <= resp.status < 400 and req.is_navigation_request():
                # Chromium would follow a navigation redirect itself, without this check.
                # Main frame: stop here and let render() follow the redirect after checking it.
                # Frames: not needed for the text, so they do not follow redirects at all.
                if req.frame == page.main_frame and resp.headers.get("location"):
                    redirect["ziel"] = urljoin(req.url, resp.headers["location"])
                    # An empty page ends this navigation cleanly (an abort would race with
                    # Chromium's error page and break the next goto).
                    route.fulfill(status=200, content_type="text/html", body="")
                else:
                    route.abort()
                return
            # Redirects of sub-requests (fetch, scripts, ...) are requested anew by the browser
            # and pass this check again.
            route.fulfill(response=resp)
        ctx.route("**/*", _route)
        try:
            target, resp = url, None
            for hop in range(MAX_REDIRECTS + 1):
                redirect["ziel"] = None
                resp = page.goto(target, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
                if not redirect["ziel"]:
                    break
                target = redirect["ziel"]
                problem = self._address_problem(target)
                if problem:
                    res.final_url, res.error = target, f"Weiterleitung gesperrt: {problem}"
                    return res
                if hop == MAX_REDIRECTS:
                    res.error = "zu viele Weiterleitungen"
                    return res
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
