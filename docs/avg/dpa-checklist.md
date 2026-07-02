# DPA-checklist — verwerkers van LO

**Status: CONCEPT / werklijst.** Versie 2026-07-02. Per leverancier: wat er
doorheen gaat, wat er geregeld moet worden, en waar. Afvinken zodra de
overeenkomst binnen is (bewaar de PDF's bij de contractadministratie).

## ✅ Microsoft 365
- **Data**: mail, agenda, taken (delegated, per-user consent via Entra-app).
- **Actie**: geen — valt onder het bestaande Microsoft-klantcontract van
  Lomans (Data Protection Addendum zit standaard in de tenant-voorwaarden).
- [x] Gedekt.

## ⬜ ORQ.AI — *prioriteit 1*
- **Data**: alle prompts en antwoorden (dus mailinhoud, namen, agenda's).
- **Waarom belangrijk**: dit is de grootste gegevensstroom van LO.
- **Gunstig**: NL/EU-bedrijf; onze modellen draaien op AWS Bedrock **EU**
  (`aws/eu.anthropic...`), dus geen VS-doorgifte voor het LLM-verkeer.
- **Actie**: DPA opvragen/tekenen via orq.ai (dashboard of sales). Expliciet
  checken: (a) geen training op klantdata, (b) log-retentie in het
  ORQ-dashboard op minimaal zetten, (c) sub-verwerkerslijst (AWS EU).
- [ ] DPA getekend · [ ] log-retentie ingesteld

## ⬜ Fireflies.ai — *prioriteit 2*
- **Data**: volledige vergadertranscripties (zwaarste categorie).
- **Let op**: VS-bedrijf → DPA + standaardcontractbepalingen (SCC's) nodig.
- **Actie**: DPA zit bij Business-abonnement (fireflies.ai/dpa); tekenen.
  In de Fireflies-instellingen: retentie beperken en "do not train"
  aanzetten. Overwegen: alleen interne meetings laten transcriberen.
- [ ] DPA+SCC's · [ ] retentie ingesteld · [ ] train-opt-out aan

## ⬜ Asana
- **Data**: taaknamen, notities, projectnamen.
- **Actie**: Asana biedt een standaard-DPA die onderdeel is van de zakelijke
  voorwaarden — verifiëren welk abonnement Lomans heeft en de DPA archiveren.
- [ ] Geverifieerd en gearchiveerd

## ⬜ Notion
- **Data**: pagina's/databases die gekoppeld zijn via de Notion-MCP.
- **Actie**: DPA beschikbaar op Business/Enterprise (notion.so/security);
  verifiëren abonnement + archiveren. AI-verwerking door Notion zelf staat
  los van LO — check de workspace-instelling.
- [ ] Geverifieerd en gearchiveerd

## ⬜ ElevenLabs — *alleen als de cloud-stem aangaat*
- **Data**: de uitgesproken antwoordtekst van LO (geen brongegevens, wel
  potentieel persoonsgegevens in antwoorden). VS-verwerking.
- **Status**: staat standaard **uit**; lokaal (XTTS/Piper) is de default.
- **Actie vóór structureel aanzetten**: betaald plan met DPA (elevenlabs.io
  → legal), en de tijdelijke API-key vervangen door een definitieve.
- [ ] DPA (pas nodig bij activering) · [ ] definitieve key

## Niet-verwerkers (ter volledigheid)
- **Neo4j (brein)**: on-premises, Community-editie — geen verwerker.
- **Cloudflare**: transporteert alleen versleuteld verkeer naar
  nova.famspaan.nl (tunnel); standaard-DPA zit in de accountvoorwaarden.
  NB: besluit eigenaar 2026-07-02 — Global API Key wordt niet geroteerd
  (bewust geaccepteerd restrisico).
- **Whisper (STT)**: draait lokaal op de z390 — geen verwerker.
