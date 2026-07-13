# src/span/orchestrator/toolretry.py
"""A3 — taak-vangnet: transient-classifier + retry met backoff voor tool-calls.

Alleen transiente fouten (429/timeout/5xx/verbindingsproblemen) komen in
aanmerking; de aanroeper (ToolBox.dispatch) beslist bovendien dat alleen
read-tools herhaald worden — een muterende tool wordt NOOIT blind opnieuw
gedraaid. Bewust klein en kort gehouden: de integraties
(span.integrations.http) doen zelf al 3 HTTP-pogingen met backoff; deze laag
vangt alleen wat daar doorheen lekt plus niet-HTTP-transients (bv. een
haperende Neo4j-verbinding), en blokkeert een worker-thread hoogstens ~3s.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

MAX_RETRIES = 2   # bovenop de eerste poging; de HTTP-laag retryt zelf al 3x
BASE_WAIT = 1.0   # backoff 1s, 2s — kort: dit draait in een beurt/worker-thread

# tekst-markers voor exceptions die als string binnenkomen (fouten van diep
# uit een integratie); bewust smal om permanente fouten nooit te herhalen
_TRANSIENT_MARKERS = (
    "429", "502", "503", "504", "timeout", "timed out",
    "connection reset", "connection refused", "connection aborted",
    "temporarily unavailable",
)


def retry_enabled() -> bool:
    """Feature-flag SPAN_TOOL_RETRY (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_TOOL_RETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def is_transient(exc: BaseException) -> bool:
    """Alleen fouten die zo weer weg kunnen zijn: throttle (429), gateway
    (502/503/504), timeout of een gevallen verbinding."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code in {429, 502, 503, 504}
    except Exception:
        pass
    name = type(exc).__name__.lower()
    if "serviceunavailable" in name or "transienterror" in name:  # neo4j-driver
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def call_with_retry(fn: Callable[[], Any],
                    max_retries: int = MAX_RETRIES,
                    base_wait: float = BASE_WAIT) -> tuple[Any, int]:
    """Voer fn uit; herhaal ALLEEN bij een transiente fout, met backoff.
    Geeft (resultaat, aantal_retries) terug. Een niet-transiente fout of de
    laatste mislukte poging gooit gewoon door — de aanroeper vertaalt dat
    (zoals nu al) naar een tool-error voor het model."""
    retries = 0
    while True:
        try:
            return fn(), retries
        except Exception as exc:
            if retries >= max_retries or not is_transient(exc):
                raise
            time.sleep(base_wait * (2 ** retries))
            retries += 1
