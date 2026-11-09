"""Turn fetched (untrusted) HTML/PDF into compact lines and schema.org events.

Only parsing happens here: no eval, no execution, no further downloads.
"""
from __future__ import annotations

import io
import json
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

MARK = "★ "
MAX_LINE = 600
MAX_PDF_PAGES = 80

# --------------------------------------------------------------- toddler filter
_KIDS = re.compile(
    r"kinder|\bkind\b|kindheit|familie|baby|babies|krabbel|kita\b|kitas|kleinkind|minis?\b|pänz|paenz|"
    r"\bab\s*\d{1,2}\s*(j\b|j\.|jahr|monat|mon\.)|\b\d\s*[-–]\s*\d\s*(j\b|j\.|jahr)|"
    r"puppe|kasper|märchen|maerchen|laterne|martin|nikolaus|flohmarkt|basar|bazar|mitmach|"
    r"bilderbuch|vorlese|eltern|spielgruppe|musikgarten",
    re.IGNORECASE,
)


def is_kids(text: str) -> bool:
    return bool(_KIDS.search(text))


def mark(line: str) -> str:
    return MARK + line if is_kids(line) else line


# --------------------------------------------------------------- HTML cleanup
DROP_TAGS = ["script", "style", "noscript", "svg", "iframe", "template", "nav", "aside", "dialog",
             "canvas", "video", "audio", "picture", "img", "object", "embed", "select", "option",
             "input", "textarea", "button", "link", "meta"]
NOISE = re.compile(
    r"cookie|consent|gdpr|borlabs|usercentrics|onetrust|cmplz|klaro|cookiebot|newsletter|breadcrumb|"
    r"skip-?link|share|social|offcanvas|off-canvas|mobile-?nav|mobile-?menu|navbar|main-?nav|"
    r"site-?header|site-?footer|page-?footer|modal|popup|banner|sidebar",
    re.IGNORECASE,
)
NOISE_ROLES = {"navigation", "banner", "contentinfo", "search", "dialog", "alertdialog", "complementary"}
KEEP = {"html", "body", "main", "article"}
BLOCK = {"p", "div", "section", "article", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
         "table", "thead", "tbody", "dt", "dd", "dl", "blockquote", "figure", "figcaption", "address",
         "main", "header", "footer", "pre", "hr", "details", "summary", "form", "fieldset", "center"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def _clean(soup: BeautifulSoup) -> None:
    for t in soup.find_all(DROP_TAGS):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    total = len(soup.get_text(" ", strip=True)) or 1

    def wraps_content(t: Tag) -> bool:
        # never drop a wrapper that holds the page's main content
        if t.find(["main", "article"]) is not None:
            return True
        return len(t.get_text(" ", strip=True)) > 0.4 * total

    for t in list(soup.find_all(True)):
        if t.decomposed or t.parent is None:
            continue
        if t.name in KEEP:
            continue
        attrs = t.attrs or {}
        ident = " ".join([str(attrs.get("id", ""))] + list(attrs.get("class", []) or []))
        noisy = (attrs.get("role") in NOISE_ROLES
                 or (t.name in ("header", "footer") and t.find_parent("article") is None)
                 or (ident.strip() and NOISE.search(ident)))
        if noisy and not wraps_content(t):
            t.decompose()


def _absolute(href: str, base: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    url = urldefrag(urljoin(base, href))[0]
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    return url


def _render(node: Tag, base: str, out: list[str]) -> None:
    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            out.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name == "br":
            out.append("\n")
            continue
        if name == "a":
            text = " ".join(child.get_text(" ").split()).replace("[", "(").replace("]", ")")
            url = _absolute(child.get("href", ""), base)
            if text and url:
                out.append(f" [{text}]({url}) ")
            elif text:
                out.append(f" {text} ")
            continue
        if name in ("td", "th"):
            out.append(" | ")
            _render(child, base, out)
            continue
        if name == "time" and child.get("datetime") and not child.get_text(strip=True):
            out.append(f" {child['datetime']} ")
            continue
        block = name in BLOCK
        if block:
            out.append("\n")
        _render(child, base, out)
        if block:
            out.append("\n")
        elif name in ("span", "strong", "b", "em", "i", "time", "small", "label"):
            out.append(" ")


def normalize_lines(text: str, ignore: list[re.Pattern] | None = None) -> list[str]:
    lines, seen = [], set()
    for raw in text.split("\n"):
        line = " ".join(raw.split())
        line = line.strip(" |·•-–—").strip()
        line = re.sub(r"\s+([,.;:!?)])", r"\1", line)
        if len(line) < 2 or not re.search(r"\w", line):
            continue
        line = EMAIL.sub("[E-Mail]", line)
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE].rstrip() + " …"
        if ignore and any(p.search(line) for p in ignore):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(mark(line))
    return lines


def cut(lines: list[str], ab: str | None, bis: str | None) -> tuple[list[str], list[str]]:
    """Keep lines from the first line matching `ab` up to (excluding) the next one matching `bis`."""
    hints = []
    strip = lambda l: l[len(MARK):] if l.startswith(MARK) else l
    if ab:
        pat = re.compile(ab, re.IGNORECASE)
        idx = next((i for i, l in enumerate(lines) if pat.search(strip(l))), None)
        if idx is None:
            hints.append(f"'ab' ({ab}) ohne Treffer, ganzer Text behalten")
        else:
            lines = lines[idx:]
    if bis:
        pat = re.compile(bis, re.IGNORECASE)
        idx = next((i for i, l in enumerate(lines) if i > 0 and pat.search(strip(l))), None)
        if idx is not None:
            lines = lines[:idx]
    return lines, hints


def html_to_lines(html: bytes | str, base_url: str, selector: str | None = None,
                  ignore: list[re.Pattern] | None = None) -> tuple[list[str], list[dict], list[str]]:
    """Return (lines, events, hints)."""
    soup = BeautifulSoup(html, "lxml")
    hints: list[str] = []
    base_tag = soup.find("base", href=True)
    base = urljoin(base_url, base_tag["href"]) if base_tag else base_url

    events = extract_events(soup, base)
    _clean(soup)

    roots: list[Tag] = []
    if selector:
        roots = soup.select(selector)
        if not roots:
            hints.append(f"Selektor '{selector}' ohne Treffer, Fallback verwendet")
    if not roots:
        for sel in ("main", "[role=main]", "#content", "#main", ".content", "#inhalt"):
            found = soup.select(sel)
            if found:
                roots = found[:1]
                break
    if not roots:
        arts = soup.find_all("article")
        roots = arts[:1] if len(arts) == 1 else [soup.body or soup]

    def render(nodes: list[Tag]) -> list[str]:
        pieces: list[str] = []
        for r in nodes:
            _render(r, base, pieces)
            pieces.append("\n")
        return normalize_lines("".join(pieces), ignore)

    lines = render(roots)
    body = soup.body or soup
    if not selector and len(lines) < 5 and roots != [body]:
        body_lines = render([body])
        if len(body_lines) > len(lines) + 5:
            hints.append("Hauptbereich fast leer, ganze Seite verwendet")
            lines = body_lines
    return lines, events, hints


# --------------------------------------------------------------- schema.org events
def _types(obj: dict) -> list[str]:
    t = obj.get("@type") or obj.get("type") or []
    return [x.rsplit("/", 1)[-1] for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _is_event(obj: dict) -> bool:
    return any(t.endswith("Event") or t == "Festival" for t in _types(obj))


def _walk(obj, out: list[dict], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, list):
        for x in obj:
            _walk(x, out, depth + 1)
    elif isinstance(obj, dict):
        if _is_event(obj):
            out.append(obj)
        for k, v in obj.items():
            if k in ("location", "offers", "organizer", "performer"):
                continue
            _walk(v, out, depth + 1)


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _text(v) -> str | None:
    v = _first(v)
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or v.get("@id")
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def _address(a) -> str | None:
    a = _first(a)
    if isinstance(a, dict):
        parts = [a.get("streetAddress"), " ".join(x for x in [_text(a.get("postalCode")),
                                                               _text(a.get("addressLocality"))] if x)]
        s = ", ".join(_text(p) for p in parts if p and _text(p))
        return s or _text(a.get("name"))
    return _text(a)


def _location(v) -> dict | None:
    v = _first(v)
    if v is None:
        return None
    if isinstance(v, dict):
        loc = {"name": _text(v.get("name")), "address": _address(v.get("address"))}
        if not any(loc.values()) and v.get("url"):
            loc["name"] = _text(v.get("url"))
        return {k: x for k, x in loc.items() if x} or None
    return {"name": _text(v)}


def _offers(v) -> list[dict] | None:
    if v is None:
        return None
    res = []
    for o in (v if isinstance(v, list) else [v]):
        if not isinstance(o, dict):
            continue
        price = o.get("price", o.get("lowPrice"))
        entry = {"price": _text(price), "priceCurrency": _text(o.get("priceCurrency"))}
        entry = {k: x for k, x in entry.items() if x is not None}
        if entry and entry not in res:
            res.append(entry)
    return res or None


def _audience(v) -> str | None:
    v = _first(v)
    if isinstance(v, dict):
        parts = []
        if v.get("audienceType"):
            parts.append(_text(v["audienceType"]))
        lo, hi = _text(v.get("suggestedMinAge")), _text(v.get("suggestedMaxAge"))
        if lo or hi:
            parts.append(f"{lo or '?'}–{hi or '?'} J.")
        return ", ".join(p for p in parts if p) or None
    return _text(v)


def normalize_event(e: dict, base: str) -> dict | None:
    name = _text(e.get("name"))
    if not name:
        return None
    url = _text(e.get("url"))
    url = _absolute(url, base) if url else None
    status = _text(e.get("eventStatus"))
    ev = {
        "name": name,
        "startDate": _text(e.get("startDate")),
        "endDate": _text(e.get("endDate")),
        "location": _location(e.get("location")),
        "offers": _offers(e.get("offers")),
        "url": url,
        "typicalAgeRange": _text(e.get("typicalAgeRange")),
        "audience": _audience(e.get("audience")),
        "eventStatus": status.rsplit("/", 1)[-1] if status else None,
    }
    ev = {k: v for k, v in ev.items() if v}
    ev["kinder"] = is_kids(" ".join(str(x) for x in [name, ev.get("typicalAgeRange"), ev.get("audience"),
                                                     _text(e.get("description"))] if x))
    return ev


def _microdata(el: Tag) -> dict:
    item: dict = {"@type": el.get("itemtype", "")}
    for d in el.find_all(attrs={"itemprop": True}):
        owner = d.find_parent(attrs={"itemscope": True})
        if owner is not el:
            continue
        if d.has_attr("itemscope"):
            val = _microdata(d)
        elif d.name == "meta":
            val = d.get("content")
        elif d.name in ("a", "link"):
            val = d.get("href")
        elif d.name == "time":
            val = d.get("datetime") or d.get_text(" ", strip=True)
        elif d.name in ("data", "meter"):
            val = d.get("value")
        else:
            val = d.get("content") or d.get_text(" ", strip=True)
        for prop in d["itemprop"].split():
            item.setdefault(prop, val)
    return item


def extract_events(soup: BeautifulSoup, base: str) -> list[dict]:
    raw: list[dict] = []
    for s in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        txt = s.string or s.get_text() or ""
        try:
            data = json.loads(txt, strict=False)
        except (ValueError, RecursionError):
            continue
        _walk(data, raw)
    for el in soup.find_all(attrs={"itemscope": True, "itemtype": re.compile(r"schema\.org/\w*Event", re.I)}):
        if el.find_parent(attrs={"itemscope": True, "itemtype": re.compile(r"Event", re.I)}):
            continue
        raw.append(_microdata(el))
    events, keys = [], set()
    for e in raw:
        ev = normalize_event(e, base)
        if not ev:
            continue
        k = event_key(ev)
        if k in keys:
            continue
        keys.add(k)
        events.append(ev)
    return events


def event_key(ev: dict) -> str:
    return "|".join([ev.get("name", ""), ev.get("startDate", "") or "", ev.get("url", "") or ""])


# --------------------------------------------------------------- PDF
def pdf_to_lines(data: bytes, ignore: list[re.Pattern] | None = None) -> tuple[list[str], list[str]]:
    from pypdf import PdfReader

    hints: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages
    if len(pages) > MAX_PDF_PAGES:
        hints.append(f"nur die ersten {MAX_PDF_PAGES} von {len(pages)} Seiten gelesen")
    texts = []
    for i, page in enumerate(pages):
        if i >= MAX_PDF_PAGES:
            break
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # broken page: skip, keep the rest
            hints.append(f"Seite {i + 1} nicht lesbar")
    return normalize_lines("\n".join(texts), ignore), hints


def links_matching(html: bytes, base_url: str, pattern: re.Pattern) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        u = _absolute(a["href"], base_url)
        if u and pattern.search(u) and u not in out:
            out.append(u)
    return out
