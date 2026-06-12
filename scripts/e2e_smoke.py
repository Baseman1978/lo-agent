"""End-to-end smoke test: echte Neo4j + echte ORQ-LLM, via de FastAPI-app.

Draait de volledige pipeline (auth, sessie-bootstrap, RAG, LLM-streaming,
briefing-endpoint) in-process. Kost één echte LLM-call.

    .venv\\Scripts\\python.exe scripts\\e2e_smoke.py
"""

from __future__ import annotations

import os
import sys

os.environ["SPAN_AUTH_TOKEN"] = "e2e-test"
HEADERS = {"Authorization": "Bearer e2e-test"}

from fastapi.testclient import TestClient  # noqa: E402

from span.server.app import app  # noqa: E402


def main() -> int:
    failures = 0
    with TestClient(app) as client:
        r = client.get("/api/status", headers=HEADERS)
        print(f"[1] /api/status -> {r.status_code} {r.json().get('counts')}")
        failures += r.status_code != 200

        r = client.get("/api/jarvis/briefing", headers=HEADERS)
        body = r.json()
        print(f"[2] /api/jarvis/briefing -> {r.status_code} "
              f"integraties={body.get('integrations')} quests={len(body.get('quests', []))} "
              f"errors={body.get('errors')}")
        failures += r.status_code != 200

        r = client.get("/api/auth/o365/status", headers=HEADERS)
        print(f"[3] /api/auth/o365/status -> {r.status_code} {r.json()}")
        failures += r.status_code != 200

        r = client.get("/api/settings", headers=HEADERS)
        s = r.json()
        print(f"[3a] /api/settings -> {r.status_code} main={s.get('model_main')} "
              f"o365={s.get('o365', {}).get('configured')}")
        failures += r.status_code != 200

        r = client.get("/api/jarvis/daily", headers=HEADERS)
        d = r.json()
        print(f"[3c] /api/jarvis/daily -> {r.status_code} spoken={len(d.get('spoken', ''))} chars")
        failures += r.status_code != 200 or not d.get("spoken")

        r = client.get("/api/graph?limit=100", headers=HEADERS)
        g = r.json()
        print(f"[3b] /api/graph -> {r.status_code} "
              f"nodes={len(g.get('nodes', []))} links={len(g.get('links', []))}")
        failures += r.status_code != 200 or not g.get("nodes")

        r = client.get("/api/status")  # zonder token, niet-localhost in TestClient
        print(f"[4] auth-guard zonder token -> {r.status_code} (verwacht 401 of 200-op-localhost)")

        print("[5] WebSocket chat — echte LLM-call…")
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "hello", "token": "e2e-test"})
            ready = ws.receive_json()
            print(f"    ready: {ready}")
            failures += ready.get("type") != "ready"
            ws.send_json({
                "type": "user",
                "text": "Systeemtest: antwoord met precies één korte zin dat alle systemen online zijn.",
            })
            deltas = 0
            answer = ""
            while True:
                msg = ws.receive_json()
                if msg["type"] == "session":
                    print(f"    sessie: {msg['session_id']} ({msg['protocols']} protocollen)")
                elif msg["type"] == "delta":
                    deltas += 1
                elif msg["type"] == "done":
                    answer = msg["answer"]
                    break
                elif msg["type"] == "error":
                    print(f"    FOUT: {msg}")
                    failures += 1
                    break
            print(f"    {deltas} stream-deltas, antwoord: {answer[:200]}")
            failures += not answer

    print("\nRESULTAAT:", "ALLES GROEN" if failures == 0 else f"{failures} FOUT(EN)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
