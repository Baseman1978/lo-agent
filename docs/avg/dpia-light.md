# DPIA (light) — LO, AI-assistent van Lomans

**Status: CONCEPT — ter toetsing door de privacy-verantwoordelijke.**
Versie 2026-07-02. Aanleiding: LO verwerkt structureel werk-mail en agenda's
van medewerkers; dat rechtvaardigt een risicobeoordeling vóór bredere uitrol.

## 1. Beschrijving van de verwerking

| Aspect | Invulling |
|---|---|
| Doel | Persoonlijke werkondersteuning: samenvatten, prioriteren, concepten, dagstart, geheugen |
| Betrokkenen | Medewerkers van Lomans die zich vrijwillig aanmelden; indirect: hun correspondenten |
| Gegevens | Mail-samenvattingen + metadata, agenda-items, taken, vergadertranscripties, chatgesprekken, afgeleide inzichten |
| Bijzondere gegevens | Niet beoogd; kunnen incidenteel in mailinhoud voorkomen (restrisico, zie §3) |
| Bewaartermijn | Brein: tot intrekking/wissen + automatische decay-instelling voor persoonsfragmenten; back-ups: 7 dagen |
| Systemen | On-premises server (z390) met Neo4j-brein per gebruiker; zie DPA-checklist voor externe verwerkers |

## 2. Noodzaak en proportionaliteit

- **Dataminimalisatie**: samenvattingen/metadata in plaats van integrale
  mailbox-kopieën; alleen gekoppelde bronnen; decay op persoonsfragmenten.
- **Vrijwilligheid**: koppeling gebeurt per medewerker via eigen
  Microsoft-login (per-user consent, geen tenant-brede uitlezing).
- **Geen personeelsmonitoring**: LO rapporteert niet aan leidinggevenden en
  wordt niet gebruikt voor beoordeling; gegevens zijn alleen zichtbaar voor
  de medewerker zelf (en technisch de beheerder).

## 3. Risico's en maatregelen

| # | Risico | Kans/Impact | Maatregel (status) |
|---|---|---|---|
| 1 | Onbevoegde toegang tot het brein | laag/hoog | Microsoft-SSO met allowlist, owner-rol voor beheer, bearer-token alleen server-side, CSP/security-headers (✅ live) |
| 2 | Cross-gebruiker-inzage bij multi-user | laag/hoog | Per-gebruiker scheiding; bij één-database-ontwerp verplichte owner-scoping + tests (ontwerp klaar, bouwen vóór uitrol) |
| 3 | Ongewenste acties namens de gebruiker | laag/middel | Goedkeuringsmodel: schrijfacties altijd via Agent Inbox; audit-keten met actor (✅ live) |
| 4 | Prompt-injectie via mail/MCP-output | middel/middel | Injectie-scan op mail, quarantaine van verdachte tool-output, lek-vangnet extern mailen (✅ live) |
| 5 | Dataverlies (single host) | middel/hoog | Nachtelijke offline dumps (✅); off-host versleutelde kopie (gepland); C2-risico bewust geaccepteerd door eigenaar |
| 6 | Doorgifte buiten EU | middel/middel | LLM-verkeer via EU-routing (ORQ→Bedrock EU); Fireflies/ElevenLabs: DPA+SCC's vereist, ElevenLabs standaard uit (zie checklist) |
| 7 | Te lange bewaring | laag/laag | Decay-instelling, backup-retentie 7 dagen, wisprocedure per gebruiker |
| 8 | Schaduwkopieën in externe AI-diensten | middel/middel | Contractueel: geen training op klantdata bedingen in DPA's (checklist-punt per leverancier) |

## 4. Conclusie (concept)

Met de bestaande technische maatregelen en de acties uit de DPA-checklist is
het restrisico **laag**, mits: (a) owner-scoping gebouwd is vóór multi-user-
uitrol, (b) de off-host back-up is ingericht, (c) DPA's met ORQ.AI en
Fireflies getekend zijn, en (d) medewerkers vooraf de interne
privacyverklaring ontvangen.

**Besluitvorming**: voorleggen aan de privacy-verantwoordelijke van Lomans;
bij akkoord jaarlijks of bij grote wijzigingen (nieuwe verwerker, nieuwe
databron) herzien.
