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

TRANSCRIPT_DETAIL_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
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
    sentences {
      speaker_name
      text
    }
  }
}
"""

DELETE_TRANSCRIPT_MUTATION = """
mutation DeleteTranscript($id: String!) {
  deleteTranscript(id: $id) {
    id
    title
  }
}
"""


class FirefliesClient:
    def __init__(self, api_key: str):
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        from span.integrations.http import request_with_retry
        resp = request_with_retry(lambda: requests.post(
            API_URL, json={"query": query, "variables": variables},
            headers=self._headers, timeout=120,
        ))
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

    def search_transcripts(self, query: str, top: int = 10,
                           scan_limit: int = 75) -> list[dict[str, Any]]:
        """Zoek in recente transcripties op zoekterm.

        LET OP (API-beperking): de Fireflies GraphQL API kent server-side
        alleen een titel-filter op de transcripts-query — geen full-text
        search over de gesproken zinnen. Daarom halen we hier de recentste
        transcripties op (max scan_limit) en zoeken we lokaal in titel +
        samenvatting (overview, actiepunten, bullets). De volledige zinnen
        van één meeting doorzoeken kan via transcript_detail."""
        import time
        q = (query or "").strip().lower()
        if not q:
            return []
        hits: list[dict[str, Any]] = []
        skip = 0
        while skip < scan_limit and len(hits) < top:
            page = self.recent_transcripts(limit=25, skip=skip)
            if not page:
                break
            for t in page:
                snippet = ""
                for label, txt in (("titel", t["title"]), ("overzicht", t["overview"]),
                                   ("actiepunten", t["action_items"]),
                                   ("bullets", t["bullets"])):
                    idx = (txt or "").lower().find(q)
                    if idx >= 0:
                        start = max(0, idx - 60)
                        snippet = f"[{label}] …{txt[start:idx + len(q) + 120]}…"
                        break
                if snippet:
                    hits.append({"id": t["id"], "title": t["title"], "date": t["date"],
                                 "participants": t["participants"], "snippet": snippet})
                    if len(hits) >= top:
                        break
            skip += len(page)
            if skip < scan_limit and len(hits) < top:
                time.sleep(1)  # rate limit: 10 req/min — rustig aan
        return hits[:top]

    def transcript_detail(self, meeting_id: str, max_chars: int = 4000) -> dict[str, Any]:
        """Samenvatting + (een deel van) de transcript-zinnen van één meeting."""
        data = self._gql(TRANSCRIPT_DETAIL_QUERY, {"id": str(meeting_id)})
        t = data.get("transcript") or {}
        if not t:
            return {"error": f"Meeting '{meeting_id}' niet gevonden bij Fireflies."}
        s = t.get("summary") or {}
        budget = max(500, int(max_chars))
        lines: list[str] = []
        total = 0
        truncated = False
        for sen in t.get("sentences") or []:
            line = f"{sen.get('speaker_name') or '?'}: {(sen.get('text') or '').strip()}"
            if total + len(line) > budget:
                truncated = True
                break
            lines.append(line)
            total += len(line) + 1
        return {
            "id": t["id"],
            "title": t.get("title") or "(zonder titel)",
            "date": t.get("dateString"),
            "duration_min": round((t.get("duration") or 0)),
            "participants": [p for p in (t.get("participants") or []) if p],
            "overview": (s.get("overview") or "").strip(),
            "action_items": (s.get("action_items") or "").strip(),
            "transcript": "\n".join(lines),
            "transcript_afgekapt": truncated,
        }

    def delete_transcript(self, meeting_id: str) -> dict[str, Any]:
        """Verwijder een transcript DEFINITIEF (deleteTranscript-mutatie).

        Bewust zonder retry: de mutatie is onomkeerbaar en niet-idempotent,
        en de Fireflies-API kent een strak rate limit (10 req/min) — een
        retry-lus zou dubbel werk of throttling uitlokken."""
        resp = requests.post(
            API_URL,
            json={"query": DELETE_TRANSCRIPT_MUTATION,
                  "variables": {"id": str(meeting_id)}},
            headers=self._headers, timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"][0].get("message", "Fireflies-fout"))
        d = (body.get("data") or {}).get("deleteTranscript") or {}
        return {"deleted": True, "id": d.get("id") or str(meeting_id),
                "title": d.get("title") or ""}

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
            skip += len(page)  # niet 25: een korte pagina zou items overslaan
            time.sleep(1)  # rustig aan met de API
        return out[:max_total]
