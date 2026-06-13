"""Span host-bridge — KIJK-MODUS (F2.7).

Draait op de Windows-HOST van Bas (NIET in de Docker-container), onder zijn
eigen gebruiker. Geeft Span een read-only blik op het scherm: een screenshot,
meer niet. GEEN muis, GEEN toetsenbord, GEEN besturing — dat is bewust de
eerste, veilige trap van de gefaseerde computer-use aanpak.

VEILIGHEID:
- Luistert alleen op 127.0.0.1 (niet op het netwerk).
- Vereist een token (zelfde SPAN_AUTH_TOKEN) in de Authorization-header.
- Doet uitsluitend GET /screenshot. Geen enkele actie die iets wijzigt.

DRAAIEN (op de pc van Bas, in een eigen venster):
    pip install --only-binary :all: pillow mss flask
    set SPAN_AUTH_TOKEN=<jouw token>
    python scripts/host_bridge.py
Daarna kan de container Span hem bereiken via http://host.docker.internal:8473
(zet SPAN_HOSTBRIDGE_URL in .env). Stop hem gewoon door het venster te sluiten.
"""

from __future__ import annotations

import base64
import io
import os

try:
    import mss
    from flask import Flask, jsonify, request
    from PIL import Image
except ImportError:
    raise SystemExit(
        "Installeer eerst: pip install --only-binary :all: pillow mss flask")

app = Flask(__name__)
TOKEN = os.environ.get("SPAN_AUTH_TOKEN", "").strip()


def _authorized() -> bool:
    if not TOKEN:
        return False  # fail-closed: zonder token geen toegang
    auth = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return auth == TOKEN


@app.get("/screenshot")
def screenshot():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])  # hele desktop
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    img.thumbnail((1280, 800))  # kleiner = minder tokens bij vision later
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return jsonify({"png_base64": base64.b64encode(buf.getvalue()).decode(),
                    "width": img.width, "height": img.height})


@app.get("/health")
def health():
    return jsonify({"ok": True, "mode": "read-only-view"})


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Zet eerst SPAN_AUTH_TOKEN in de omgeving.")
    # alleen localhost; de container bereikt dit via host.docker.internal
    app.run(host="127.0.0.1", port=8473)
