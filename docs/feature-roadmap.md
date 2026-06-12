# Span — 100 features (status: 12 juni 2026)

**Geïmplementeerd (✅ ~50):** 1, 3, 4, 5, 7 (deels via conflicten), 8, 11, 12 (concept-stijl), 13, 14, 15 (deels), 17 (via triage-regels), 20 (prompt), 21 (detectie), 23, 24 (prompt), 30, 31, 32 (deels), 33, 34, 35, 36, 37, 39 (temporele props), 40, 53, 54, 55 (prompt), 57, 61, 62, 63, 64, 65, 67 (deels), 70, 71, 79 (weer), 81 (prompt), 83 (prompt), 89, 91, 92, 93 (QR/token-basis), 95 (JSON-export), 99 — plus Agent Inbox-stembediening en entiteit-extractie naar het hologram.

**Geblokkeerd op externe input van Bas:** 72 (Teams-tenant), 75 (Home Assistant), 76 (extensie-distributie), 77 (Fireflies), 25 (Maps-key), 58/93-https (domein/certificaat).

**Open (grote L-projecten of laagprio):** zie lijst hieronder.

Gebaseerd op deep research (22 bronnen, 110 claims geëxtraheerd, 23 adversarieel
geverifieerd — juni 2026). Rode draad 2025-2026: van chat-first naar **ambient
agents** (event-gedreven, Agent Inbox met Notify/Question/Review), graph-native
geheugen op Neo4j is nu een officieel patroon, en document-grounded Q&A over
bouwdata is een bewezen commerciële categorie.

Complexiteit: **S** = uren, **M** = 1-3 dagen, **L** = week+.
Binnen elke categorie gerangschikt op waarde.

## 1. Ambient & proactief (event-gedreven i.p.v. chat-gedreven)

1. **Agent Inbox** (M) — goedkeuringswachtrij in de HUD: Span zet voorgenomen acties (mail versturen, afspraak verzetten) klaar met accept/edit/ignore; het geverifieerde kern-UX-patroon voor ambient agents, past direct op onze WebSocket.
2. **Graph-webhooks** (M) — Microsoft Graph change notifications op inbox/agenda: Span reageert binnen seconden op nieuwe mail i.p.v. te wachten op een vraag.
3. **Notify/Question/Review-protocol** (S) — drie interactietypen in het systeemprompt + HUD-onderscheid; canoniek veiligheidspatroon.
4. **Triggers-engine** (M) — gebruikersregels in natuurlijke taal ("als mail van X over project Y, waarschuw direct") opgeslagen als Protocol-nodes, geëvalueerd per event.
5. **Avondafsluiting** (S) — tegenhanger van de dagstart: om 17:00 wat is blijven liggen + voorstel voor morgen.
6. **Wachtrij-besluiten via voice** (S) — "Span, keur het eerste concept goed" — Agent Inbox bedienbaar met stem.
7. **Conflictradar** (M) — continue check op botsingen tussen agenda, Asana-deadlines en quests; melding vóór het knelt.
8. **Weekreview-generator** (S) — vrijdagmiddag: wat is af, wat schoof door, automatisch als Insight opgeslagen.
9. **Locatiebewuste dagstart** (M) — telefoon-HUD geeft reistijd naar eerste afspraak (Maps API) mee in de briefing.
10. **Stilte-modus met opslag** (S) — focus-uren: Span verzamelt meldingen en levert ze gebundeld na afloop.

## 2. E-mail copilot (O365 al gekoppeld)

11. **Triage-pijplijn** (M) — apart classificatie-stadium (respond/notify/ignore) vóór de respons-agent; het geverifieerde referentie-ontwerp, port van Gmail-blueprint naar Graph.
12. **Concepten in jouw stem** (M) — schrijfstijl leren uit sent items (fragmenten in het brein), concepten klaargezet in Agent Inbox.
13. **Follow-up tracker** (M) — wie heeft na N dagen niet geantwoord → herinneringsconcept klaar; exact wat Outlook Copilot (preview) doet, wij via Graph.
14. **Thread-samenvatting** (S) — lange mailthread → 5 regels + openstaande vragen.
15. **Bijlage-naar-brein** (M) — PDF/Word-bijlagen samenvatten en als MemoryFragment koppelen aan afzender/project.
16. **Inbox-regels voorstellen** (M) — Span herkent patronen ("je archiveert nieuwsbrief X altijd") en stelt een Graph messageRule voor.
17. **VIP-detectie** (S) — leert wie belangrijk is uit antwoordsnelheid/frequentie; weegt mee in triage.
18. **Snooze met context** (S) — "herinner me hieraan als project X start" — gekoppeld aan quest-status i.p.v. alleen datum.
19. **Ontvangstbevestiging-radar** (S) — detecteert vragen in jouw verzonden mail die onbeantwoord blijven.
20. **Mail→Asana-taak** (S) — één commando: mail wordt taak met link, deadline-suggestie uit de inhoud.

## 3. Agenda & planning

21. **Conflict-oplosser** (M) — overlappende 1:1's: Span stelt hersteltijden voor op basis van beider beschikbaarheid (findMeetingTimes), klaargezet ter goedkeuring.
22. **Focus-blokken** (S) — automatisch deep-work blokken plannen rond deadline-druk uit Asana/quests.
23. **Meeting prep-kaart** (M) — 15 min vooraf: deelnemers + alles wat het brein over hen/het onderwerp weet + relevante mails; uniek door onze graph.
24. **Vergader-debrief** (S) — na afloop vraagt Span om 2 regels uitkomst → MemoryFragment + actiepunten.
25. **Reistijd-bewaking** (M) — afspraken op locatie krijgen automatisch reisblokken; bouwplaatsbezoek herkend uit adres.
26. **Agenda-gezondheid** (S) — wekelijks: % vergadertijd vs focustijd, trend in de HUD.
27. **Invite-assistent** (M) — binnenkomende uitnodigingen beoordeeld tegen prioriteiten; voorstel accepteren/afslaan met reden.
28. **Slimme herplanning** (L) — bij ziekte/uitval: hele dag herschikken met één goedkeuring.
29. **Terugkerende 1:1-agenda** (S) — gesprekspunten verzamelen per persoon tussen overleggen door (uit mail/chat/brein).
30. **Deadline-vooruitblik** (S) — "wat knelt er volgende week" als vast dagstart-onderdeel.

## 4. Geheugen & zelflerend (Neo4j — onze unieke kracht)

31. **Reasoning-traces** (M) — redeneerstappen + tool-gebruik opslaan met :TOUCHED-edges naar entiteiten (officieel Neo4j Labs-patroon); Span leert van eigen redeneringen.
32. **Voorkeur-geheugen met feedback-loop** (M) — correcties van Bas worden Preference-nodes die toekomstig gedrag bijsturen; hét self-evolving mechanisme uit de literatuur.
33. **Bi-temporeel geheugen** (M) — event-tijd én ingest-tijd op MemoryFragments (TSM/Graphiti-patroon, tot 12% accuracywinst op long-memory benchmarks).
34. **Nachtelijke consolidatie** (M) — clusteren, dedupliceren, promoveren naar Insights/Skills; zelfreinigend brein.
35. **Entiteit-extractie** (M) — personen/projecten/bedrijven als eigen nodes met relaties (KNOWS, WORKS_AT), automatisch uit gesprekken.
36. **Geheugen-verval** (S) — relevantie-score die zakt zonder gebruik; vervaagde fragmenten uit bootstrap, niet uit de graph.
37. **Contradictie-detectie** (M) — nieuw fragment botst met oud → Span vraagt welke waar is.
38. **"Wat weet je over X"-kaart** (S) — entiteit-pagina in de HUD: alle kennis, relaties en historie rond een persoon/project.
39. **Sessie-tijdreizen** (S) — "wat bespraken we vorige week dinsdag" — temporele queries op de graph.
40. **Brein-statistieken-trend** (S) — groeigrafiek van het geheugen in de HUD; gamification van de cirkel.

## 5. Lomans / bouwdata (werk-Neo4j — niemand anders heeft dit)

41. **Document-grounded Q&A** (L) — vragen over tekeningen/documenten beantwoord mét bronverwijzing uit de werk-graph; bewezen categorie (Trunk Tools: 90% submittals automatisch beoordeeld — vendorclaim).
42. **Revisie-diff-melder** (M) — nieuwe tekeningrevisie → wat veranderde er, wie moet het weten.
43. **Projectradar** (M) — dagelijkse signalen: documenten zonder review, assets zonder status, stilgevallen projecten.
44. **Locatie-dossier** (S) — voor bouwplaatsbezoek: alles over die locatie (assets, tekeningen, open punten) als prep-kaart.
45. **Requirements-check** (L) — eisen uit het werk-graph naast documenten leggen; afwijkingen rapporteren.
46. **Asset-zoekassistent** (S) — "welke luchtbehandelingskasten staan in gebouw X" — natuurlijke taal naar work_cypher.
47. **Tekening-metadata-verrijking** (M) — koppel mail/besluiten uit het brein aan tekeningnummers in de werk-graph (cross-graph linking).
48. **Onderhoudskalender-signaal** (M) — assets met vervaldatums → automatisch in dagstart en agenda.
49. **Project-tijdlijn-visual** (M) — werk-graph data als tijdlijn in de HUD naast het brein-hologram.
50. **Mail↔project-matching** (M) — inkomende mail automatisch herkend als horend bij project Y; triage weegt projectdeadlines mee.

## 6. Voice & spraak

51. **Server-side Whisper STT** (L) — faster-whisper op de server i.p.v. Web Speech API: betrouwbaarder NL, werkt op telefoon-HUD zonder https-beperking (isair/jarvis-patroon, geverifieerd haalbaar op consumer hardware).
52. **LLM intent-judge** (M) — licht model classificeert transcripten (gericht aan Span? echo? stopcommando?) i.p.v. fuzzy matching; geverifieerd patroon.
53. **Streaming TTS-zinnen** (S) — antwoord per zin voorlezen tijdens het streamen i.p.v. wachten op het einde.
54. **Stemprofielen** (S) — settings: stemkeuze, tempo, formeel/informeel per moment van de dag.
55. **Voice-memo naar brein** (S) — "Span, onthoud dit:" gevolgd door vrij dicteren → MemoryFragment.
56. **Doorlopende gespreksmodus** (M) — rolling context window: meerdere beurten zonder wake word zolang het gesprek loopt.
57. **Audio-notificaties met aard** (S) — verschillende chimes voor mail/agenda/waarschuwing; herkenbaar zonder kijken.
58. **Realtime speech-to-speech** (L) — OpenAI Realtime/Azure Voice Live voor sub-300ms; de grote sprong, pas als de rest staat.
59. **Spraak op telefoon via PWA + https** (M) — self-signed of tailscale-https zodat getUserMedia mobiel werkt.
60. **Meertalige modus** (S) — Engels herkennen en beantwoorden voor internationale contacten.

## 7. HUD & visualisatie

61. **Hologram-filters** (S) — toggle per node-type, tijd-slider ("toon brein zoals vorige maand"), zoeken in de graph.
62. **Hologram-volgmodus** (S) — tijdens gesprek lichten de nodes op die Span raadpleegt (:TOUCHED live); brein zichtbaar aan het denken.
63. **Notificatie-toasts** (S) — ambient events verschijnen als holografische kaarten naast de chat.
64. **Command palette** (S) — Ctrl+K: alle acties/panelen/instellingen doorzoekbaar.
65. **Thema's** (S) — Mark III (rood/goud), War Machine (grijs), Arc Blue; CSS-variabelen staan al klaar.
66. **Vergrote dagstart-overlay** (S) — ochtendbriefing als fullscreen cinematic kaart met agenda-tijdlijn.
67. **Systeemstats-paneel** (S) — LLM-latency, token-gebruik, ORQ-kosten vandaag, brein-omvang.
68. **Picture-in-picture mini-Span** (M) — klein zwevend reactor-venster (Document PiP API) dat meeluistert terwijl je in andere apps werkt.
69. **Tijdlijn-lens** (M) — chat-historie + fragmenten + sessies als scrollbare tijdlijn.
70. **Boot-persoonlijkheid** (S) — bootsequence varieert met echte systeemstatus (aantal nieuwe mails, brein-groei vannacht).

## 8. Integraties & kanalen

71. **Telegram-bridge** (M) — Span op zak: chat, voice notes, dagstart als ochtendbericht; grootste wow per regel code.
72. **Teams-integratie** (L) — chats/kanalen lezen via Graph, samenvattingen, mentions in triage.
73. **SharePoint-zoeken** (M) — documenten vinden en samenvatten via Graph search; Lomans-documenten direct bereikbaar.
74. **Webhook-in endpoint** (S) — generiek /api/inbound voor externe systemen (CI, monitoring, domotica) → ambient events.
75. **Home Assistant** (M) — "werkdag voorbij"-scene, aanwezigheid als context voor de dagstart.
76. **Browser-extensie** (M) — rechtsklik "vraag Span" / "onthoud dit" op elke pagina.
77. **Fireflies/transcripties** (M) — vergaderverslagen automatisch naar het brein met actiepunt-extractie.
78. **OneDrive-bestanden as tool** (M) — zoeken/lezen in eigen bestanden via Graph.
79. **Weer + verkeer in dagstart** (S) — open-meteo + ANWB-feed; relevant voor bouwplaatsbezoek.
80. **iCal-publicatie** (S) — Span-gegenereerde focus-blokken als abonneerbare agenda.

## 9. Taken, quests & productiviteit

81. **Commitment-tracker** (M) — beloftes uit gesprekken/mail als Commitment-nodes met deadline-bewaking in de dagstart.
82. **Asana tweerichting-sync** (M) — quest ↔ Asana-taak gekoppeld; status loopt automatisch mee.
83. **Taak-decompositie** (S) — grote taak → stappenplan als quest met substappen, na goedkeuring.
84. **Prioriteiten-matrix** (S) — Eisenhower-view over Asana + To Do + quests gecombineerd.
85. **Tijdsbesteding-reflectie** (M) — agenda-categorieën vs uitgesproken prioriteiten; wekelijkse spiegel.
86. **Delegatie-volger** (M) — taken die je uitzet bij anderen (mail/Asana) gevolgd tot afronding.
87. **Energie-bewust plannen** (S) — voorkeuren leren (ochtendmens?) en zware taken daar plannen.
88. **Sjabloon-quests** (S) — terugkerende processen (projectoplevering, onboarding) als herbruikbare quest-templates.
89. **Blokkade-detectie** (S) — quest zonder beweging in N dagen → Span vraagt wat er knelt.
90. **Pomodoro met reactor** (S) — focustimer waarbij de arc reactor als countdown-visual dient.

## 10. Platform, beveiliging & beheer

91. **Audit-log** (S) — elke uitgevoerde actie (mail verstuurd, afspraak gemaakt) als log-node; "wat heb je gisteren namens mij gedaan".
92. **Autonomie-niveaus** (M) — per actietype instelbaar: alleen melden / concept klaarzetten / autonoom; Outlook Copilot's suggest-and-approve als default.
93. **HTTPS via Caddy/Tailscale** (M) — veilige externe toegang; ontgrendelt mobiele microfoon en webhooks.
94. **Multi-device sessies** (M) — zelfde gesprek doorzetten van PC naar telefoon.
95. **Backup & restore van het brein** (S) — nachtelijke Neo4j-dump met retentie; één-klik herstel.
96. **Kosten-dashboard** (S) — ORQ-verbruik per dag/model; budget-alarm.
97. **Lokale LLM-fallback** (L) — Ollama-route voor privacygevoelige taken of ORQ-storing (geverifieerd haalbaar patroon).
98. **Prompt-injectie-wacht** (M) — inkomende mail/documenten gescand op instructies aan de agent vóór ze in context gaan.
99. **Health-monitor met zelfherstel** (S) — Span detecteert eigen storingen (Neo4j weg, ORQ traag) en meldt + herstart services.
100. **Plugin-architectuur voor tools** (L) — tools als losse modules met registratie-decorator (sukeesh-patroon); nieuwe integraties zonder core-wijziging.

## Bronnen (geverifieerd)

- [LangChain — Introducing ambient agents](https://blog.langchain.com/introducing-ambient-agents/) · [agents-from-scratch](https://github.com/langchain-ai/agents-from-scratch) · [Agent Inbox](https://github.com/langchain-ai/agent-inbox)
- [Neo4j Labs — agent-memory](https://github.com/neo4j-labs/agent-memory) · [mcp-neo4j-agent-memory](https://github.com/knowall-ai/mcp-neo4j-agent-memory)
- [Microsoft — agentic Copilot in Outlook](https://techcommunity.microsoft.com/blog/outlook/copilot-in-outlook-new-agentic-experiences-for-email-and-calendar/4514601) (preview; suggest-and-approve)
- [Trunk Tools](https://trunktools.com/) (AEC document-Q&A; percentages = vendorclaims)
- [arXiv 2601.07468 — Temporal Semantic Memory](https://arxiv.org/pdf/2601.07468) (preprint) · [arXiv 2507.21046 — Self-evolving agents survey](https://arxiv.org/pdf/2507.21046)
- [isair/jarvis](https://github.com/isair/jarvis) (lokale voice-stack, geverifieerd actief)

**Gesneuveld in verificatie** (niet op gebouwd): "Outlook Copilot werkt volledig autonoom" (0-3 weerlegd — het is suggest-and-approve preview) en "duration-typed memory edges lossen een bewezen beperking op" (0-3 weerlegd).
