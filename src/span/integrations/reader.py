"""Web-reader (F2.2) en web-search (F2.1).

reader: haalt een publieke webpagina op en stript tot leesbare tekst. SSRF-
veilig (geen interne/private adressen, alleen http/https), en de opgehaalde
tekst is ALTIJD untrusted — de aanroeper haalt 'm door de quarantaine-laag.

web-search: via Tavily (TAVILY_API_KEY). Zonder key een nette melding, net als
de andere optionele integraties.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from span.safety.egress import is_public_host

MAX_BYTES = 2_000_000
MAX_TEXT = 8000
MAX_REDIRECTS = 4


class _Strip(HTMLParser):
    """Minimalistische HTML->tekst (stdlib, geen ARM64-wheel-risico)."""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _is_public_url(url: str) -> bool:
    """SSRF-bescherming: alleen http(s) naar een publiek IP. Blokkeert
    localhost, private ranges, link-local en cloud-metadata (169.254.x)."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    return is_public_host(p.hostname)


def fetch_readable(url: str) -> dict:
    """Haal een publieke pagina op en geef leesbare tekst terug.
    Retourneert {ok, url, text|error}. De text is UNTRUSTED.

    SSRF-hardening (I1): geen automatische redirects — elke hop wordt opnieuw
    gevalideerd, zodat een toegestane publieke URL niet kan doorsturen naar een
    intern/metadata-adres. (Resterend rebinding-venster is klein: alle A-records
    worden gecontroleerd.)"""
    if not _is_public_url(url):
        return {"ok": False, "url": url,
                "error": "Geweigerd: alleen publieke http(s)-URLs (geen interne adressen)."}
    cur = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            resp = requests.get(cur, timeout=15, stream=True, allow_redirects=False,
                                headers={"User-Agent": "Span/1.0 (persoonlijke assistent)"})
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                resp.close()
                if not loc:
                    break
                nxt = urljoin(cur, loc)
                if not _is_public_url(nxt):
                    return {"ok": False, "url": url,
                            "error": "Geweigerd: redirect naar een niet-publiek/intern adres."}
                cur = nxt
                continue
            resp.raise_for_status()
            raw = resp.raw.read(MAX_BYTES, decode_content=True)
            html = raw.decode(resp.encoding or "utf-8", errors="replace")
            break
        else:
            return {"ok": False, "url": url, "error": "Geweigerd: te veel redirects."}
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"Ophalen mislukt: {exc}"}
    parser = _Strip()
    parser.feed(html)
    text = " ".join(parser.parts)[:MAX_TEXT]
    return {"ok": True, "url": cur, "text": text}


def web_search(query: str, max_results: int = 5) -> dict:
    """Web-search via Tavily. Resultaten (titel/url/snippet) zijn untrusted."""
    import os
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return {"ok": False,
                "error": "Web-search niet geconfigureerd (zet TAVILY_API_KEY in .env; "
                         "gratis tier op tavily.com)."}
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query,
                  "max_results": max(1, min(int(max_results), 8))},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"Zoeken mislukt: {exc}"}
    return {"ok": True, "query": query,
            "results": [{"title": r.get("title"), "url": r.get("url"),
                         "snippet": r.get("content")}
                        for r in data.get("results", [])]}
