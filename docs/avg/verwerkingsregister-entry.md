# Verwerkingsregister-regel (art. 30 AVG) — LO, AI-assistent

**Status: CONCEPT** — over te nemen in het verwerkingsregister van Lomans.
Versie 2026-07-02.

| Veld | Invulling |
|---|---|
| **Naam verwerking** | LO — interne AI-werkassistent |
| **Verwerkingsverantwoordelijke** | Lomans B.V. |
| **Beheerder/contact** | Bas Spaan (b.spaan@lomans.nl) |
| **Doeleinden** | Persoonlijke werkondersteuning van medewerkers: samenvatten en prioriteren van mail/agenda, conceptteksten, taakbeheer, vergaderverslagen, dagelijkse briefing, persoonlijk werkgeheugen |
| **Categorieën betrokkenen** | Deelnemende medewerkers (vrijwillige koppeling); indirect hun zakelijke correspondenten |
| **Categorieën gegevens** | Zakelijke e-mail (samenvattingen + metadata), agenda-items, taken, projectpagina's, vergadertranscripties, chatgesprekken met de assistent, afgeleide werkinzichten |
| **Grondslag** | Gerechtvaardigd belang (efficiënte bedrijfsvoering) + vrijwillige activatie per medewerker; geen gebruik voor personeelsbeoordeling |
| **Ontvangers/verwerkers** | Microsoft (M365), ORQ.AI (LLM-gateway, EU) → AWS Bedrock EU (Anthropic-modellen), Asana, Notion, Fireflies (transcripties), optioneel ElevenLabs (spraak, standaard uit) — zie DPA-checklist |
| **Doorgifte buiten EER** | LLM-verkeer: nee (EU-routing). Fireflies en ElevenLabs: VS — op basis van DPA + standaardcontractbepalingen |
| **Bewaartermijnen** | Brein-gegevens: zolang de medewerker deelneemt, met automatische veroudering (decay) van persoonsfragmenten; back-ups 7 dagen; bij vertrek/intrekking: verwijdering, uiterlijk na de back-upretentie volledig |
| **Technische en organisatorische maatregelen** | On-premises opslag; Microsoft-SSO met allowlist; rollen (beheerder vs. medewerker); goedkeuringsmodel voor alle uitgaande acties; manipulatie-bestendige audit-keten; injectie-scan en output-quarantaine; versleuteld transport; nachtelijkse offline back-ups; CI met tests |
| **DPIA** | Uitgevoerd (light), zie `docs/avg/dpia-light.md`; herziening jaarlijks of bij nieuwe verwerker/databron |
