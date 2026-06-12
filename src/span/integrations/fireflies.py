"""Fireflies.ai — vergadertranscripties via de GraphQL API.

FIREFLIES_API_KEY in .env (Fireflies → Integrations → Fireflies API).
Span haalt samenvattingen + actiepunten op; de volledige transcriptie
blijft bij Fireflies, alleen de essentie gaat het brein in.
"""

from __future__ import annotations

from typing import Any

import requests

API_URL = "https://api.fireflies.ai/graphql"

TRANSCRIPTS_QUERY = """
query Transcripts($limit: Int, $skip: Int) {
  transcripts(limit: $limit, skip: $skip) {
    id
    title
    dateString
    duration
    participants
    summary {
      overview
      action_items
      shorthand_bullet
    }
  }
}
"""


class FirefliesClient:
    def __init__(self, api_key: str):
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = None
        for attempt in range(2):  # de API is traag bij paginatie — één retry
            try:
                resp = requests.post(
                    API_URL, json={"query": query, "variables": variables},
                    headers=self._headers, timeout=120,
                )
                break
            except requests.Timeout:
                if attempt == 1:
                    raise
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"][0].get("message", "Fireflies-fout"))
        return body["data"]

    def recent_transcripts(self, limit: int = 10, skip: int = 0) -> list[dict[str, Any]]:
        data = self._gql(TRANSCRIPTS_QUERY,
                         {"limit": min(int(limit), 25), "skip": int(skip)})
        out = []
        for t in data.get("transcripts") or []:
            s = t.get("summary") or {}
            out.append({
                "id": t["id"],
                "title": t.get("title") or "(zonder titel)",
                "date": t.get("dateString"),
                "duration_min": round((t.get("duration") or 0)),
                "participants": [p for p in (t.get("participants") or []) if p],
                "overview": (s.get("overview") or "").strip(),
                "action_items": (s.get("action_items") or "").strip(),
                "bullets": (s.get("shorthand_bullet") or "").strip(),
            })
        return out

    def all_transcripts(self, max_total: int = 200) -> list[dict[str, Any]]:
        """Volledige historie, gepagineerd per 25."""
        import time
        out: list[dict[str, Any]] = []
        skip = 0
        while len(out) < max_total:
            page = self.recent_transcripts(limit=25, skip=skip)
            if not page:
                break
            out.extend(page)
            skip += 25
            time.sleep(1)  # rustig aan met de API
        return out[:max_total]
