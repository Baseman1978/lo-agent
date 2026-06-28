# Werkplan — Microsoft SSO + Multi-user NOVA

> Status: ontwerp / roadmap. Opgesteld 2026-06-27.
> Doel: één Microsoft-login (lomans.nl) opent je NOVA én logt meteen alle
> M365-connectors in; later krijgt elke gebruiker een eigen, privé NOVA met
> daarnaast een gedeeld geheugen.

## Beslissingen (vastgelegd)
1. **Login-poort:** de Microsoft-login *ín* Span (OIDC) wordt de enige login.
   Cloudflare Access wordt verwijderd; Span bewaakt zelf de toegang.
2. **Tenant:** authenticatie via **lomans.nl** (Azure/Entra van Lomans).
3. **Isolatie:** **privacy-first** — een privé Neo4j-database per gebruiker
   (`brain-<oid>`) **plus** één centrale **`brain-shared`** voor gedeeld geheugen.
4. **Toegang:** lomans.nl-accounts; aanvullende allowlist voor wie een NOVA krijgt
   (nader te bevestigen).

## Huidige architectuur (samengevat uit de code)
- Edge: Cloudflare Access (e-mail-code) → tunnel → Span.
- App-auth: één gedeelde `SPAN_AUTH_TOKEN` (HMAC), geen user-identiteit
  (`server/state.py`).
- Server: één globale `_state` (één brein, één LLM, één O365-client), gedeeld
  door alle verbindingen (`server/app.py`).
- M365: MSAL **device-code**, één token-cache, één account (`integrations/o365.py`).
- Brein: één Neo4j-db `span-brain`, één `Identity` (`db/brain.py`, `db/schema.py`).
- → Fundamenteel **single-user**.

## Doelarchitectuur
- **Login:** OIDC + OAuth2 *authorization-code* met PKCE tegen de lomans.nl-tenant.
  Eén aanmelding levert: ID-token (identiteit) + access/refresh-token (Graph).
  → identiteit én connectors in één stap; geen device-code meer.
- **Sessie:** ondertekende, httpOnly sessie-cookie; vervangt `SPAN_AUTH_TOKEN`.
- **Per-user context:** de globale `_state` wordt opgesplitst naar een context per
  gebruiker (sleutel = Entra `oid`): eigen brein, eigen O365-client (eigen token),
  eigen runtime-staat.
- **Token-opslag:** per gebruiker, versleuteld at-rest (refresh-tokens).
- **Brein:** `brain-<oid>` privé per gebruiker + `brain-shared` centraal.
  Lezen = privé ∪ gedeeld; schrijven standaard naar privé, expliciet "deel met
  team" schrijft naar `brain-shared`.
- **Exposure:** Cloudflare Tunnel blijft (bereikbaarheid); Cloudflare Access eraf.

## ⚠️ Kritieke afhankelijkheid / risico's
- **R1 — Entra-app in lomans.nl:** dit vereist een app-registratie in de
  **lomans.nl-tenant** (web-redirect `https://nova.famspaan.nl/auth/callback`,
  client-secret, scopes `openid profile email offline_access` + Graph
  Mail/Calendar/Tasks). **Heb jij Entra-admin bij Lomans?** Zo niet, dan moet
  Lomans-IT de app registreren of admin-consent geven. Dit kan WP-0/WP-1 blokkeren.
  (De bestaande "Jarvis OS"-app zit in BIM Energy, niet Lomans — dus die kan niet
  zomaar hergebruikt worden tenzij we multi-tenant + admin-consent doen.)
- **R2 — Access eraf = Span is de enige poort:** de OIDC-flow, allowlist en
  sessie-beveiliging moeten waterdicht zijn (geen token-lek, state/nonce/PKCE,
  secure cookies). Hardening in WP-4 is niet optioneel.
- **R3 — z390-netwerk hapert:** los van dit plan, maar relevant voor uptime.

## Roadmap / werkplannen

### WP-0 — Entra-app & fundament
- Bevestig Entra-admin/route bij lomans.nl (zie R1).
- Registreer/krijg app in lomans.nl: redirect-URI, client-secret, scopes,
  app-type = confidential web.
- Beslis allowlist-bron (config/env vs. brein).
- **Acceptatie:** app-gegevens (client-id/secret/tenant) beschikbaar; testlogin
  via browser geeft een auth-code op de callback.

### WP-1 — SSO-login (nog één brein) → levert wens #1 + #2
- `server/auth.py`: `/auth/login` (redirect naar Entra), `/auth/callback`
  (code→tokens, validatie nonce/state/PKCE), ondertekende sessie-cookie.
- Vervang `_check_token`/`SPAN_AUTH_TOKEN`-gate door sessie-check
  (`server/state.py`, `server/app.py` WS + REST).
- O365-client voeden vanuit de sessie-token i.p.v. device-code
  (`integrations/o365.py` uitbreiden met een "from access token"-pad).
- **Acceptatie:** na Microsoft-login opent de HUD en zijn mail/agenda meteen
  gekoppeld; geen device-code; geen `SPAN_AUTH_TOKEN` meer nodig.

### WP-2 — Per-user context
- Refactor globale `_state` → context per `oid` (resolver in WS/REST).
- Per-user versleutelde token-store; O365-client per gebruiker.
- **Acceptatie:** twee verschillende logins zien elkaars connectors/sessie niet.

### WP-3 — Per-user brein + gedeeld geheugen → levert wens #3
- Neo4j database-per-user (`CREATE DATABASE brain-<oid>`) + bootstrap
  (`init_schema`/identity) bij eerste login.
- `brain-shared` centraal; lees-unie privé ∪ gedeeld; expliciete "deel"-actie.
- `BrainDB`/`memory/*`/`orchestrator/*` gebruiken de per-user + shared db.
- **Acceptatie:** elke gebruiker een eigen privé-brein; gedeelde kennis zichtbaar
  voor allen; geen privé-lek tussen gebruikers.

### WP-4 — Multi-user beheer & hardening
- Allowlist/admin, sessie-beheer (logout/expiry), per-user audit,
  token-encryptie at-rest, rate-limiting, security-review.
- **Acceptatie:** alleen toegestane lomans.nl-accounts; logout werkt; audit per
  gebruiker; security-review akkoord.

### WP-5 — Migratie & cutover
- Huidige `span-brain` (Bas) → zijn `brain-<oid>`; relevante kennis → `brain-shared`.
- Docs bijwerken; Cloudflare Access definitief uit; productie-cutover.
- **Acceptatie:** live op nova.famspaan.nl met SSO + multi-user; oude single-user
  paden uitgefaseerd.

## Volgorde-logica
WP-1 levert al je directe wens (Microsoft-login → connectors meteen). WP-2/3
voegen multi-user + privé/gedeeld brein toe. WP-0 (lomans.nl-app) is de
blokkerende voorwaarde — daar eerst duidelijkheid over.
