# Span installeren op de x64-machine

> De first-run setup-wizard (login óf API in de HUD) komt in WP-3 van de
> SDK-transitie. Tot dan: deze minimale `.env` + `docker compose up`.

## 1. Repo + .env
```
git clone <repo>
cd "AI agent"
# maak een .env in de root met onderstaande inhoud
docker compose up -d --build
```

## 2. Minimale `.env` (alleen wat verplicht is om te starten)
```env
# --- INFRA (verplicht; gedeeld met de Neo4j-container, nodig bij opstart) ---
NEO4J_PASSWORD=kies-een-sterk-wachtwoord
SPAN_AUTH_TOKEN=kies-een-lange-willekeurige-token      # HUD/CLI-toegang (LAN)

# --- LLM (verplicht om te werken) ---
ORQ_API_KEY=je-orq-sleutel
# optioneel; defaults staan al goed:
# SPAN_MODEL_MAIN=anthropic/claude-opus-4-8
# SPAN_MODEL_LIGHT=aws/eu.anthropic.claude-haiku-4-5-20251001-v1:0
# SPAN_EMBED_MODEL=openai/text-embedding-3-large
# SPAN_EMBED_DIMS=1024

# --- AUDIT (aanbevolen; anders auto-gegenereerd bij eerste start) ---
# SPAN_AUDIT_HMAC_KEY=eigen-geheim-los-van-de-auth-token
```

## 3. Optioneel (integraties — leeg laten = uit)
```env
# Microsoft 365 (eigen app-registratie; anders de publieke client):
# MS_CLIENT_ID=
# MS_TENANT_ID=common
# Asana:
# ASANA_TOKEN=
# ASANA_WORKSPACE=
# Fireflies (vergaderverslagen):
# FIREFLIES_API_KEY=
# Telegram-bridge:
# TELEGRAM_BOT_TOKEN=
# Web-search:
# TAVILY_API_KEY=
# Werk-Neo4j (read-only bouwdata):
# WORK_NEO4J_URI=
# WORK_NEO4J_USER=neo4j
# WORK_NEO4J_PASSWORD=
# Geheugen-verval: off | soft | log
# SPAN_DECAY=off
```

## 4. Claude-abonnement (voor de SDK-transitie, later)
Voor de overstap naar de Claude Agent SDK op je abonnement:
```
scripts\claude-login.bat        # -> CLAUDE_CODE_OAUTH_TOKEN (zet in .env)
pip install claude-agent-sdk
python scripts\spike_sdk.py     # meet de 3 SDK-punten (zie werkplan WP-1)
```

## 5. Meenemen van je huidige brein (optioneel)
- `documents/` (geüploade verslagen) en de **Neo4j-data** zitten in Docker-volumes,
  niet in git. Wil je je huidige brein meenemen: kopieer het Neo4j-data-volume of
  maak een dump op de oude machine en importeer op de x64. Anders start je vers.

## Wat NIET in git zit (bewust)
`.env`, `documents/`, `viz-demos/`, Neo4j-data — secrets/persoonlijke data blijven lokaal.
