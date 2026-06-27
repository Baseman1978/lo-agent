# Ontwerp — Gedeeld geheugen (`brain-shared`)

> Status: ontwerp (2026-06-27). Het "speciale stukje" bovenop WP-2 (multi-user).
> Doel: naast ieders **privé**-brein (`brain-<oid>`) één **gedeeld** brein
> (`brain-shared`) voor teamkennis. **Privacy-first:** privé is de standaard;
> delen is een bewuste actie.

## Uitgangspunten
- **Lezen = privé ∪ gedeeld.** Span haalt context uit het eigen brein én het
  gedeelde brein, samengevoegd en ontdubbeld.
- **Schrijven = privé.** Alles wat Span vastlegt (MemoryFragments uit gesprekken,
  mail/agenda-afgeleide inzichten) gaat standaard naar `brain-<oid>`.
  Mail/agenda-inhoud komt **nooit** automatisch in het gedeelde brein.
- **Delen is expliciet.** Een knoop (Insight, Skill, Protocol, Idea, Fragment)
  belandt alleen in `brain-shared` via een bewuste "deel met team"-actie.

## Hoe het nu werkt (grond)
- `memory/fragments.py` — `FragmentStore(brain, llm)`: `search()` doet
  `brain.vector_search("mf_embedding", …)` op `MemoryFragment`; `search_formal()`
  over Insight/Mistake/Idea-indexen; `write()` maakt MemoryFragment + `FROM_SESSION`.
- `memory/bootstrap.py` — `load_bootstrap()`: Identity, Protocollen, open Quests,
  Skills, recente Insights/Mistakes + `fragments.search(eerste_vraag)`.
- Alles draait tegen **één** `BrainDB`. WP-2 levert al `ctx.brain` (privé) en
  `registry.shared_brain()` (het gedeelde brein).

## Doelmodel
Elke gebruikers-context krijgt twee breinen: **`ctx.brain`** (privé, read/write)
en **`ctx.shared`** (gedeeld, read; write alleen via de deel-actie).

### 1. Lezen (union)
- **`FragmentStore`** krijgt een optionele lijst extra (read-only) breinen:
  `FragmentStore(brain, llm, extra_brains=[shared])`. `search()`/`search_formal()`
  draaien de vector-query op **elk** brein, voegen de hits samen, ontdubbelen
  (op `id`/inhoud) en sorteren op score → top-k. Shared-hits krijgen een vlag
  `source="shared"` voor de HUD/provenance.
- **`load_bootstrap`** voegt gedeelde Protocollen/Skills/Quests toe aan de privé.
  Voorstel: gedeelde **Protocollen + Skills** altijd meenemen (teamafspraken),
  gedeelde **Insights** alleen via de vector-match (relevantie), niet de volledige lijst.

### 2. Schrijven / delen
- Nieuwe tool in de orchestrator: **`share_memory(node_id, scope="team")`** —
  kopieert de knoop (props + embedding) van `ctx.brain` naar `brain-shared`,
  met herkomst: `shared_by=<oid>`, `shared_at`, en een `ORIGIN`-edge/`origin_id`
  naar de bron. Idempotent (zelfde bron → update i.p.v. duplicaat).
- HUD: een "deel met team"-knop op een Insight/Skill/Protocol-kaart die die tool
  aanroept (na bevestiging, net als de Agent Inbox-flow).
- **Terugtrekken:** `unshare_memory(node_id)` verwijdert de gedeelde kopie.

### 3. Privacy & governance
- `brain-shared` bevat **alleen expliciet gedeelde** knopen — nooit ruwe
  MemoryFragments uit mail/agenda tenzij iemand ze bewust deelt.
- Herkomst altijd zichtbaar (`shared_by`), zodat duidelijk is wie wat inbracht.
- Optioneel later: rollen (wie mag delen / wie mag het gedeelde brein beheren).

### 4. Schema
- `brain-shared` gebruikt hetzelfde `init_schema` (zelfde labels + vector-indexen),
  zodat `vector_search` identiek werkt. Geen `Identity`-knoop nodig (het is een
  kennispool, geen agent); of één `Identity {name:'Team'}` als anker.

## Concrete wijzigingen (later, als "speciaal stukje")
1. `memory/fragments.py`: `extra_brains`-parameter + union/merge in
   `search`/`search_formal` (read-only op de extra breinen).
2. `memory/bootstrap.py`: shared Protocollen/Skills/relevante Insights mee-laden.
3. `server/usercontext.py`: `UserContext.shared` veld; registry vult 'm met
   `shared_brain()`.
4. `orchestrator/tools.py` + `tool_specs.py`: `share_memory`/`unshare_memory`
   (+ TOOL_META-groep "Gedeeld geheugen").
5. `server/app.py` (WS): `FragmentStore(ctx.brain, llm, extra_brains=[ctx.shared])`
   en bootstrap met shared.
6. HUD: deel-knop + `source="shared"`-markering in de panelen.
7. Tests: union-merge ontdubbelt + sorteert; `share_memory` kopieert + idempotent;
   privé-fragment lekt niet naar shared.

## Open ontwerpkeuzes (voor jou)
- **A. Trigger om te delen:** alleen een HUD-knop (mens beslist), óf mag Span
  zelf voorstellen te delen (via de Agent Inbox, jij keurt goed)?
- **B. Wat standaard mee uit shared in de bootstrap:** alleen team-Protocollen +
  Skills (aanrader), of ook breder?
- **C. Beheer:** mag iedereen in de allowlist delen/terugtrekken, of alleen de
  owner/een beheerder?

Zie ook `WERKPLAN-SSO-MULTIUSER.md` (WP-3) en de multi-user-fundamenten in
`server/usercontext.py`.
