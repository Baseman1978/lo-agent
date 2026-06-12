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


def request_with_retry(
    do_request: Callable[[], requests.Response],
    attempts: int = 3,
    max_wait: float = 30.0,
) -> requests.Response:
    """Voer een request-callable uit; retry op 429/502/503/504 en op
    verbindingsfouten, met Retry-After-respect en exponentiële backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = do_request()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == attempts:
                raise
            time.sleep(min(2 ** attempt, max_wait))
            continue
        if resp.status_code not in RETRYABLE or attempt == attempts:
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        try:
            wait = float(retry_after)
        except ValueError:
            wait = float(2 ** attempt)
        time.sleep(min(max(wait, 1.0), max_wait))
    raise last_exc or RuntimeError("request_with_retry: onbereikbaar")
