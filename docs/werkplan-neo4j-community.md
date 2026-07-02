# Werkplan — Neo4j Enterprise → Community (C1, licentie-schoon + AVG-arm)

**Besluit (Bas, 2026-07-02):** geen Enterprise-licentie (~€15k+/jr) en geen AuraDB
(cloud = extra verwerker, AVG-risico). LO gaat naar **Neo4j Community** (gratis,
ook commercieel) met **één database**; data blijft op eigen hardware (z390).

## Waarom dit kan

- Live draait LO **single-user**: `SPAN_MULTIUSER` staat uit, `ctx.shared = None`
  → de app gebruikt alleen `span-brain`. `brain-shared` bevat 9 testnodes.
- Community mist alleen features die we niet (meer) nodig hebben: multi-database
  (vervangen door fase 2-ontwerp), online backup (we dumpen al offline), RBAC.

## Fase 1 — migratie naar Community (uitgevoerd 2026-07-02)

**Gotcha die alles bepaalt:** de databases staan in **block**-store-formaat
(Enterprise-default sinds 5.14); Community kan dat niet lezen. Converteer dus
*vóór* de overstap, met Enterprise-tooling, naar `aligned`.

Stappen (z390):
1. Verse offline dumps van alle db's naar `~/nova-sync-backup/` (rollback + archief).
2. Nog op Enterprise: `STOP DATABASE` → `neo4j-admin database copy span-brain
   spanaligned --to-format=aligned` → dump `spanaligned` → hernoem naar
   `span-brain.dump`. Zelfde voor `brain-shared` (alleen archief).
3. Nieuw volume `nova-neo4j-community-data`; one-off `neo4j:5-community`-container:
   `neo4j-admin database load span-brain --from-path=...` (vóór eerste start).
4. Compose: `image: neo4j:5-community`, `NEO4J_initial_dbms_default__database:
   span-brain`, volume omgezet. Enterprise-volume blijft onaangeraakt staan =
   rollback (compose terugdraaien is genoeg).
5. Verify: `SHOW DATABASES` (community, span-brain online), nodecount 1199,
   span-container healthy, `/readyz` ready.

Code-kant: `BrainDB.ensure_database()` checkt nu eerst `SHOW DATABASES` (werkt op
Community) en probeert pas CREATE als de db ontbreekt. Repo-compose staat op
community als referentie voor verse installaties.

## Fase 2 — multi-user in één database (bouwen vóór uitrol naar collega's)

Het db-per-gebruiker-ontwerp (WP-2, `brain-<oid>`) vervalt; isolatie verhuist
naar app-niveau. Ontwerp:

- **Eigenaarschap**: elke node/relatie krijgt `owner: <oid>` (gedeeld = `shared`).
  Indexes op `owner` + bestaande keys.
- **Chokepoint**: `ContextRegistry` levert een `ScopedBrain` die `owner` als
  verplichte parameter injecteert; queries lopen via helpers die het filter
  afdwingen. Tests die verifiëren dat elke tool-query owner-gescoped is.
- **Delen** (`memory_share`): kopie krijgt `owner: "shared"` + `shared_by`
  in dezelfde db (vervangt brain-shared; de 9 archief-nodes desgewenst her-importeren).

### AVG-minimalisatie (uitgangspunt van Bas)

1. **Data blijft on-prem** (z390) — geen extra verwerker; DPA-lijstje blijft kort
   (ORQ.AI, Microsoft, Asana, Fireflies — zie B5).
2. **Dataminimalisatie**: mail/agenda alleen als samenvatting/metadata in het
   brein, niet integraal; bestaande decay/retentie-instelling (`sec-decay`)
   standaard aan voor persoonsfragmenten.
3. **Recht op vergetelheid**: per gebruiker wissen = één query
   (`MATCH (n {owner: $oid}) DETACH DELETE n`) + audit-regel. Makkelijker dan
   met db-per-user (DROP kan niet selectief in dumps terugkijken).
4. **Backups**: dumps bevatten straks álle gebruikers → vóór off-host-kopie
   versleutelen (rclone crypt of age) en retentie 7 dagen houden; wissen-verzoek
   betekent ook: wachten tot retentie de backups uitspoelt (vastleggen in B5).
5. **Toegangsscheiding**: owner-only-routes (WP-A1) + audit-actor (WP-B4) staan;
   ScopedBrain sluit cross-user-lekken op queryniveau.

**Status fase 2: nog niet gestart** — bouwen zodra uitrol naar collega's concreet
wordt. Geschat: 2-3 dagdelen (ScopedBrain + owner-migratie + sharing + tests).
