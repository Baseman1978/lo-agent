"""Gedeelde HTTP-weerbaarheid voor alle integraties.

Eén throttle-moment (429) of een haperende dienst (503) mag een dagstart of
agent-tool niet direct laten vallen: respecteer Retry-After en probeer het
een paar keer met backoff. Alles daarbuiten is aan de aanroeper.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

RETRYABLE = {429, 502, 503, 504}


def guarded_get(url: str, **kwargs) -> requests.Response:
    """GET met egress-allowlist (F1.5): weigert niet-toegestane hosts vóór de
    request de deur uit gaat. Gebruik dit voor URLs die uit untrusted content
    kunnen komen (web-fetch, reader-modus)."""
    from span.safety.egress import EgressBlocked, url_allowed
    if not url_allowed(url):
        raise EgressBlocked(f"uitgaand verkeer naar niet-toegestane host geweigerd: {url}")
    return request_with_retry(lambda: requests.get(url, **kwargs))


def request_with_retry(
    do_request: Callable[[], requests.Response],
    attempts: int = 3,
    max_wait: float = 30.0,
    idempotent: bool = True,
) -> requests.Response:
    """Voer een request-callable uit; retry met Retry-After-respect en backoff.

    idempotent=True (GET/PATCH-by-id): retry op 429/502/503/504 + connectie/timeout.
    idempotent=False (niet-idempotente POST — mail sturen, afspraak maken): NIET
    herhalen bij een Timeout (de server kan de actie al verwerkt hebben -> dubbele
    mail/uitnodiging) of gateway-fouten; alleen 429 (throttle, zeker níét verwerkt)
    en een ConnectionError (verbinding faalde vóór verzenden) zijn dan veilig."""
    retryable = RETRYABLE if idempotent else {429}
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = do_request()
        except requests.ConnectionError as exc:   # vóór verzenden gefaald -> veilig
            last_exc = exc
            if attempt == attempts:
                raise
            time.sleep(min(2 ** attempt, max_wait))
            continue
        except requests.Timeout as exc:
            last_exc = exc
            if not idempotent or attempt == attempts:
                raise   # dubbelzinnig: mogelijk al verwerkt -> niet blind opnieuw
            time.sleep(min(2 ** attempt, max_wait))
            continue
        if resp.status_code not in retryable or attempt == attempts:
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        try:
            wait = float(retry_after)
        except ValueError:
            wait = float(2 ** attempt)
        time.sleep(min(max(wait, 1.0), max_wait))
    raise last_exc or RuntimeError("request_with_retry: onbereikbaar")
