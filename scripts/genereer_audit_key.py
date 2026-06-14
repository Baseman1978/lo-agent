"""Genereer een audit-HMAC-sleutel en zet 'm in .env (SPAN_AUDIT_HMAC_KEY).

Wordt aangeroepen door genereer_audit_key.bat. Maakt een sterke willekeurige
sleutel, schrijft/vervangt de regel in .env (in de projectmap) en toont de
vervolgstappen. Idempotent qua regel: een bestaande SPAN_AUDIT_HMAC_KEY wordt
vervangen (= sleutelrotatie -> daarna her-ankeren).
"""
from __future__ import annotations

import secrets
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"
KEY = "SPAN_AUDIT_HMAC_KEY"


def main() -> None:
    value = secrets.token_urlsafe(48)
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    had = any(ln.strip().startswith(KEY + "=") for ln in lines)
    lines = [ln for ln in lines if not ln.strip().startswith(KEY + "=")]
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{KEY}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=" * 60)
    if had:
        print("LET OP: bestaande SPAN_AUDIT_HMAC_KEY VERVANGEN (sleutelrotatie).")
    print(f"Nieuwe {KEY} weggeschreven naar:")
    print(f"  {ENV}")
    print("=" * 60)
    print()
    print("Vervolgstappen (de keten her-ankeren onder de nieuwe sleutel):")
    print("  docker compose restart span")
    print("  docker exec span-agent python /app/scripts/reanchor_audit.py")
    print()
    print("De sleutelwaarde zelf staat veilig in .env (niet hier getoond).")


if __name__ == "__main__":
    main()
