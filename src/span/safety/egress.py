"""Egress-allowlist (F1.5).

Beperkt waarheen Span uitgaand verkeer mag sturen. Sluit exfiltratie-kanalen:
een gekaapte tool kan geen data naar een willekeurige host lekken. De lijst is
uitbreidbaar via SPAN_EGRESS_EXTRA (komma-gescheiden hostnamen).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

# Vaste, bekende bestemmingen van de integraties.
_ALLOWED = {
    "api.orq.ai",
    "graph.microsoft.com", "login.microsoftonline.com",
    "app.asana.com",
    "api.fireflies.ai",
    "api.open-meteo.com", "geocoding-api.open-meteo.com",
    "api.telegram.org",
}


def _allowed_hosts() -> set[str]:
    extra = os.environ.get("SPAN_EGRESS_EXTRA", "")
    return _ALLOWED | {h.strip().lower() for h in extra.split(",") if h.strip()}


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


class EgressBlocked(RuntimeError):
    """Uitgaand verkeer naar een niet-toegestane host."""
