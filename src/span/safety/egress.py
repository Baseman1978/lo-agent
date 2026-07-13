"""Egress-allowlist (F1.5).

Beperkt waarheen Span uitgaand verkeer mag sturen. Sluit exfiltratie-kanalen:
een gekaapte tool kan geen data naar een willekeurige host lekken. De lijst is
uitbreidbaar via SPAN_EGRESS_EXTRA (komma-gescheiden hostnamen).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Vaste, bekende bestemmingen van de integraties.
_ALLOWED = {
    "api.orq.ai",
    "graph.microsoft.com", "login.microsoftonline.com",
    "api.powerbi.com",
    "app.asana.com",
    "api.fireflies.ai",
    "api.open-meteo.com", "geocoding-api.open-meteo.com",
    "api.telegram.org",
    "graph.facebook.com",   # A6: WhatsApp Cloud API (messages/media)
    "lookaside.fbsbx.com",  # A6: WhatsApp media-download-URLs
    "api.tavily.com",
}

# Hosts die Bas BEWUST koppelt (bv. een MCP-server) komen er tijdens runtime bij.
# Een host die alleen uit untrusted metadata komt (een token_endpoint uit een
# well-known-respons op een vreemde host) staat hier NIET in -> wordt geweigerd.
_RUNTIME_ALLOWED: set[str] = set()


def allow_host(host: str) -> None:
    """Voeg een bewust-gekoppelde host toe aan de runtime-allowlist."""
    h = (host or "").lower().strip()
    if h:
        _RUNTIME_ALLOWED.add(h)


def _allowed_hosts() -> set[str]:
    extra = os.environ.get("SPAN_EGRESS_EXTRA", "")
    return (_ALLOWED | _RUNTIME_ALLOWED
            | {h.strip().lower() for h in extra.split(",") if h.strip()})


def host_allowed(host: str) -> bool:
    host = (host or "").lower().strip()
    if not host:
        return False
    allowed = _allowed_hosts()
    # exacte match of subdomein van een toegestane host
    return any(host == a or host.endswith("." + a) for a in allowed)


def url_allowed(url: str) -> bool:
    try:
        return host_allowed(urlparse(url).hostname or "")
    except Exception:
        return False


def is_public_host(host: str) -> bool:
    """True als elke DNS-resolutie van host een publiek IP geeft. Blokkeert
    localhost, private ranges, link-local en cloud-metadata (169.254.x).
    Onresolveerbaar of één privé-IP => False (fail-closed)."""
    host = (host or "").strip()
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def assert_egress(url: str) -> None:
    """Bindende egress-poort voor gevoelige, naar-buiten-gaande calls (OAuth,
    MCP-RPC). Eist https + een allowlisted host + uitsluitend publieke IP's.
    Sluit SSRF (intern/metadata-adres) én blind token-lek (vreemde host uit
    untrusted metadata) af. Raise EgressBlocked bij weigering."""
    try:
        p = urlparse(url)
    except Exception:
        raise EgressBlocked(f"ongeldige URL: {url!r}")
    if p.scheme != "https":
        raise EgressBlocked(f"alleen https toegestaan, niet {p.scheme!r}: {url}")
    host = p.hostname or ""
    if not host_allowed(host):
        raise EgressBlocked(f"host niet op de allowlist: {host}")
    if not is_public_host(host):
        raise EgressBlocked(f"host resolved naar een niet-publiek/intern adres: {host}")


class EgressBlocked(RuntimeError):
    """Uitgaand verkeer naar een niet-toegestane host."""
