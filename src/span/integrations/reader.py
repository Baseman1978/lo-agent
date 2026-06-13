"""Web-reader (F2.2) en web-search (F2.1).

reader: haalt een publieke webpagina op en stript tot leesbare tekst. SSRF-
veilig (geen interne/private adressen, alleen http/https), en de opgehaalde
tekst is ALTIJD untrusted — de aanroeper haalt 'm door de quarantaine-laag.

web-search: via Tavily (TAVILY_API_KEY). Zonder key een nette melding, net als
de andere optionele integraties.
"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

MAX_BYTES = 2_000_000
MAX_TEXT = 8000


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
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except Exception:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return False
    return True


def fetch_readable(url: str) -> dict:
    """Haal een publieke pagina op en geef leesbare tekst terug.
    Retourneert {ok, url, text|error}. De text is UNTRUSTED."""
    if not _is_public_url(url):
        return {"ok": False, "url": url,
                "error": "Geweigerd: alleen publieke http(s)-URLs (geen interne adressen)."}
    try:
        resp = requests.get(url, timeout=15, stream=True,
                            headers={"User-Agent": "Span/1.0 (persoonlijke assistent)"})
        resp.raise_for_status()
        raw = resp.raw.read(MAX_BYTES, decode_content=True)
        html = raw.decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"Ophalen mislukt: {exc}"}
    parser = _Strip()
    parser.feed(html)
    text = " ".join(parser.parts)[:MAX_TEXT]
    return {"ok": True, "url": url, "text": text}


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
