# Span HUD — 100 JARVIS special effects

Gebaseerd op deep research (23 bronnen-claims adversarieel geverifieerd, 12 juni 2026):
interviews met de échte Iron Man HUD-ontwerpers (Territory Studio, Jayse Hansen,
Cantina Creative), three.js/GLSL-recepten, MDN Web Audio, canonieke CodePens.

## Drie geverifieerde ontwerpwetten (uit de filmstudio's zelf)

1. **Ambient vs. hero** (Territory Studio): veel subtiele achtergrond-effecten die
   coherent aanvoelen + enkele "hero"-momenten van ±3 seconden die iets vertellen
   (boot, alert, dagstart). Nooit alles tegelijk laten schreeuwen.
2. **Elk effect communiceert een event** (Jayse Hansen, "non-usable interface"):
   film-UI's tonen wat de computer dóét. Elk effect hieronder is daarom gekoppeld
   aan een systeemgebeurtenis — denken, vinden, falen, luisteren.
3. **Glitch = signaal, geen sfeer** (Territory, Endgame): glitch alleen bij
   schade/fouten. Diepte-effecten spaarzaam — "too much dimensionality is
   distracting" (Cantina). Dynamiek in 2D wint van fake-3D.

Complexiteit: **S** = uren · **M** = 1-3 dagen · **L** = week+.
✅ = al in Span aanwezig.

## 1. Arc reactor & kern (canvas 2D)

1. ✅ Multi-ring reactor met counter-rotation, spokes, spoelen — S
2. ✅ Audio-reactieve kern (mic-level → schaal/glow) — S
3. **Reactor-states met kleurtaal** — denken=sneller cyan, fout=rood flikkeren, succes=groene puls; event-gedreven (wet 2) — S
4. **Charge-up bij lange taken** — ring vult als progressbar tijdens tool-loops, "vol" = antwoord klaar — S
5. **Energie-arcs** — willekeurige bliksempjes tussen ringen bij hoge activiteit (lijnsegmenten met jitter) — S
6. **Reactor-flare** — bij dagstart een 1.5s burst (radiale gradient + schokgolf-ring die uitdijt en vervaagt) — S
7. **Magnetische deeltjes** — particles in spiraalbaan rond de kern bij luisteren (polar coords) — S
8. **Ring-segmenten als systeemmeters** — buitenring verdeeld in segmenten die brein/mail/agenda-status tonen — M
9. **Reactor-echo bij chime** — elke chime stuurt een zichtbare ring naar buiten, synchroon met audio — S
10. **Slaapstand-ademen** — bij 5 min inactiviteit traagt alles naar 0.3x en dimt 40%, "wakker worden" bij input — S

## 2. Achtergrond & atmosfeer (canvas 2D)

11. ✅ Hexgrid + particles + scanning lines — S
12. **Parallax-diepte** — 3 particle-lagen op verschillende snelheid, muispositie verschuift lagen licht — S
13. **Constellatie-lijnen** — particles binnen 80px krijgen verbindingslijn (afstand→opacity); klassiek, hoog effect/moeite — S
14. **Data-rain in marges** — dunne kolommen vallende glyphs (Matrix-stijl, maar cyan en ijl) alleen in schermranden — S
15. **Nevel-banken** — 2-3 grote, trage radiale gradients die over elkaar drijven (compositing 'lighter') — S
16. **Grid-puls vanuit interactie** — klik/commando stuurt rimpeling door het hexgrid (afstand-tot-epicentrum als golf) — M
17. **Sterrenkaart-modus** — 's avonds (na 18:00) wisselt de achtergrond naar sterrenveld met langzame rotatie — S
18. **Topografische contourlijnen** — langzaam morfende hoogtelijnen (simplex noise → marching squares) — M
19. **Vluchtpad-strepen** — af en toe een lichtstreep die diagonaal passeert (komeet met staart), max 1 per 30s — S
20. **Weersynchronisatie** — regenkans uit de dagstart → subtiele druppel-particles; storm = meer wind in particles — S

## 3. Tekst & typografie

21. ✅ Typewriter boot-sequence — S
22. **TextScramble-decode** (soulwire-recept: per teken random start/duur, 28% flikkerkans per frame) op paneel-titels bij verversen — S
23. **Decode-on-arrival** — elke toast/inbox-titel scramblet 0.4s naar leesbaar — S
24. **Count-up cijfers** — brein-stats tellen op naar hun waarde (ease-out) i.p.v. verschijnen — S
25. **Glyph-flicker op labels** — 1 willekeurig teken per paneel-titel flikkert elke ~10s heel kort naar een symbool — S
26. **Spraak-ondertiteling** — wat Span uitspreekt verschijnt woord-voor-woord groot onderin (karaoke-stijl, sync met TTS-events) — M
27. **Terminal-cursor** — blinkende ▍na het laatste gestreamde woord tijdens delta's — S
28. **Belangrijke woorden oplichten** — namen/datums/bedragen in antwoorden krijgen cyan glow (regex + mark) — S
29. **Foutmeldingen in CRT-stijl** — warn-berichten krijgen chromatic aberration (rood/blauw text-shadow offset) — S
30. **Tijdstempel-decay** — oudere chatberichten vervagen licht en krijgen scanline-textuur (wet 1: ambient) — S

## 4. Panelen & frames (CSS)

31. ✅ Clip-path hoekframes + backdrop-blur — S
32. **Animated conic-gradient borders** — draaiende lichtrand om actieve panelen (conic-gradient + @property, GPU-composited) — S
33. **Hoek-brackets die "vastklikken"** — bij paneel-focus schuiven 4 hoekhaken van buiten naar de hoeken — S
34. **Staggered reveal** — panelen klappen na boot één voor één open (scale-y 0→1 met 120ms stagger) — S
35. **Holografische shimmer** — diagonale lichtstreep glijdt elke ~20s over één paneel (translate van gradient-overlay) — S
36. **Frame-schade bij fouten** — paneel met error krijgt 1 "gebroken" hoek (clip-path morph) tot opgelost — wet 3 — M
37. **Levende paneellijnen** — border bestaat uit segmenten die af en toe herpositioneren (SVG stroke-dasharray animatie) — M
38. **Focus-dimming** — actief paneel 100% helder, rest zakt naar 60% (hero vs ambient, wet 1) — S
39. **Blueprint-modus** — toggle die alles naar wireframe schakelt: borders zichtbaar, vullingen weg, raster eroverheen — S
40. **Micro-leader-lines** — lijntje + label verbindt toast met het paneel waar hij over gaat (SVG overlay) — M

## 5. Brein-hologram (three.js / WebGL)

41. ✅ Force-graph met kleurtaal, auto-rotate, touched-highlight, type-filters — M
42. **HolographicMaterial op nodes** (Mancini's vanilla drop-in: fresnel edge-glow + scanlines + additive blending) — dé upgrade — M
43. **UnrealBloomPass** — echte glow om alles (duur op zwakke GPU's: toggle in settings, half-res, nMips=3) — M
44. **Vertex-glitch bij fouten** — nodes vervormen in golven (vertex shader displacement) als het brein onbereikbaar was — M
45. **Entrance-animatie nieuwe nodes** — scale 0→1 ease-out cubic 600ms + glow-burst 2.7x→1x 800ms (obsidian-jarvis-recept) — S
46. **Pulse-propagatie** — touched node pulst, puls reist via edges naar buren (BFS met delay per hop) — denken wordt letterlijk zichtbaar — M
47. **Point-cloud morphing** — brein morpht tussen vormen (bol↔brein↔logo) via twee position-attributes + uProgress-mix in GLSL (per-frame JS-updates zijn te duur, geverifieerd) — L
48. **Camera-cinematografie** — bij touched: camera vliegt 600ms ease-out naar het cluster, daarna terug — S
49. **Scanline-pass over hologrampaneel** — fullscreen shader-pass alleen op de hologram-canvas (CRT-gevoel) — M
50. **Constellatie-labels** — bij stilstand faden type-groepslabels in als sterrenbeeld-namen — S
51. **Orbit-satellieten** — 2-3 kleine lichtpunten in baan om de Identity-node (Span's "bewustzijn") — S
52. **Geheugen-leeftijd als diepte** — oude nodes drijven naar buiten/vervagen, verse blijven dicht bij de kern — S
53. **Verbindings-flits bij nieuwe relatie** — nieuwe edge tekent zich als lichtspoor van A naar B — M
54. **Hologram-projectie-flicker** — hele graph flikkert 2 frames bij refresh (opacity dip), alsof de projector hapert — S
55. **God rays vanuit de kern** — radiale lichtstralen achter de Identity-node (radial blur shader of cheap: canvas gradient-spikes) — M

## 6. Audio-reactief (Web Audio API — geverifieerde MDN-recepten)

56. ✅ Mic-level → reactor/particles — S
57. **Siri-waveform tijdens luisteren** (SiriWave: dependency-vrij canvas 2D, setAmplitude per frame vanuit AnalyserNode) — onderin het scherm — S
58. **Oscilloscoop tijdens spreken** — fftSize 2048 + getByteTimeDomainData (÷128 normaliseren) als golflijn door het reactorpaneel — S
59. **Radiale frequency bars om de reactor** — fftSize 256, 128 bins als spaken (hoogte = dataArray[i]/2) — S
60. **TTS-mond** — tijdens spreken: gesimuleerde amplitude uit zinslengte/tempo moduleert de waveform (TTS geeft geen audio-stream) — S
61. **Beat-flash** — energie-piek in lage bins → 1-frame glow-boost op het hele canvas — S
62. **Stilte-detectie visueel** — hoe langer stil tijdens luistermodus, hoe verder de waveform inzakt tot platte lijn — S
63. **Chime-synthese zichtbaar** — elke chime tekent zijn eigen golfvorm 0.5s in een hoek (oscillator → analyser → canvas) — S
64. **Spraakherkenning-confidence** — interim results kleuren de waveform: onzeker=dim, zeker=fel — M
65. **Ruisvloer-kalibratie** — eerste 2s mic meet de ruisvloer; visuals reageren alleen boven die drempel — S

## 7. Status, meters & data-widgets (canvas/CSS)

66. ✅ Health-dot, latency-stat, threat-stijl badge — S
67. **Ring-gauges** — brein-stats als concentrische voortgangsringen (stroke-dasharray of canvas arc) — S
68. **Radar sweep** — klein rond widget: draaiende sweep, blips = open inbox-items op "afstand" (urgentie) — S
69. **Sparklines** — 7-dagen trend onder elke stat (mini polyline, data uit sessie-historie) — M
70. **ECG-hartslag** — systeemhartslag-lijn die per heartbeat-interval een puls tekent; flatline = brein offline — S
71. **Lissajous-figuur** — klein "denkpatroon"-widget dat parameters verandert per state (a/b-ratio uit activiteit) — S
72. **Klok met orbitale ringen** — seconden/minuten/uren als drie roterende bogen om de digitale tijd — S
73. **Token/kosten-teller** — count-up van LLM-calls vandaag met odometer-rol-animatie — S
74. **Verbindings-latency als afstand** — ping naar server bepaalt hoe "ver" een satelliet-icoon van het centrum staat — S
75. **Kalender-tijdlijn-balk** — horizontale dagbalk met afspraak-blokken, NU-naald die echt beweegt — M

## 8. Interactie & cursor

76. ✅ Command palette, hover-states — S
77. **Targeting reticle cursor** — custom cursor: cirkel + kruisdraad die bij klikbare elementen "lockt" (vierkant eromheen + hoekhaken) — S
78. **Klik-rimpel** — elke klik zaait een uitdijende ring op klikpunt (canvas overlay) — S
79. **Magnetische knoppen** — knoppen trekken licht naar de cursor binnen 30px (transform translate) — S
80. **Lock-on bij voice-commando** — herkend commando → reticle flitst naar het relevante paneel en lockt 0.5s — M
81. **Drag-hologram** — versleepbare panelen met "projectie-residu" (ghost-trail die vervaagt) — M
82. **Scroll-momentum-lijnen** — snelheidslijnen in de marge tijdens snel scrollen in de chat — S
83. **Lange-druk radiaal menu** — hold op een bericht → cirkelmenu (onthoud/taak/voorlezen) opent als waaier — M
84. **Toets-echo** — bij typen lichten random hexgrid-cellen onder de invoerbalk kort op — S
85. **Confirm-haptiek visueel** — goedkeuring in Agent Inbox: vinkje tekent zichzelf (SVG stroke-dashoffset) + ring — S

## 9. Hero-momenten (±3 seconden, wet 1)

86. ✅ Boot sequence + glitch bij fouten — S
87. **Dagstart-overlay** — fullscreen: datum decodeert, agenda-tijdlijn bouwt op, weer-icoon, daarna inklappen naar HUD — M
88. **Alert-takeover** — urgente melding: scherm dimt, rode scanline-veeg, alert-kaart klapt centraal open met decode-tekst — M
89. **Sessie-evaluatie-cinematic** — bij /end: hologram zoomt in, nieuwe Insight-nodes verschijnen één voor één met flits — M
90. **Wake-word acknowledge** — "Jarvis" gehoord: alle panelen pulsen 1x synchroon naar de gebruiker toe (scale 1.01) + chime — S
91. **O365-koppel-ceremonie** — gelukte login: Microsoft-kleurenpulse door het hexgrid, panelen vullen één voor één — S
92. **Nieuwe-skill-fanfare** — brein promoveert iets naar Skill: ster-burst in het hologram + toast met decode — S
93. **Shutdown-sequence** — /end: panelen klappen omgekeerd dicht, reactor spint af, "SYSTEMEN IN RUST" — S
94. **Verjaardags/feestdag-modus** — dagstart kent de datum: confetti-particles in themakleur op speciale dagen — S
95. **Threat-mode** — (speels) "rode modus" commando: heel even War Machine-thema + alarm-sweep, daarna terug — S

## 10. Systeembreed & performance

96. **prefers-reduced-motion respect** — alle ambient effecten uit bij OS-instelling; hero's blijven (toegankelijkheid) — S
97. **Effect-budget manager** — centrale FPS-monitor: zakt frame-rate < 50, dan ambient-effecten getrapt uitschakelen — M
98. **OffscreenCanvas voor achtergrond** — fx-canvas naar worker-thread, main thread vrij voor chat — M
99. **Dirty-flag rendering hologram** — alleen renderen bij verandering/rotatie (obsidian-jarvis-patroon: spaart ~60 renders/s) — S
100. **Effect-instellingen in ⚙** — schuif "ambient-intensiteit" (uit/subtiel/vol) + hero-toggles; Cushings afleidingsgrens is persoonlijk — S

## Aanbevolen eerste batch (spektakel/moeite)

1. #57+58 Siri-waveform + oscilloscoop (audio-reactief, geverifieerde recepten)
2. #22-24 TextScramble + count-up (heel veel JARVIS-gevoel voor weinig code)
3. #42 HolographicMaterial op het brein (drop-in bestand)
4. #46 Pulse-propagatie door edges (denken zichtbaar — uniek)
5. #77 Targeting reticle cursor
6. #87 Dagstart-overlay (hero-moment van de dag)
7. #97+100 Effect-budget + instellingen (fundament om de rest veilig te stapelen)

## Geverifieerde bronnen

- [Territory Studio Q&A — scifiinterfaces.com](https://scifiinterfaces.com/2020/06/23/scifi-interfaces-qa-with-territory-studio/) (ambient/hero, glitch=schade)
- [Jayse Hansen interview — TNW](https://thenextweb.com/news/jayse-hansen-on-creating-tools-the-avengers-use-to-fight-evil-touch-interfaces-and-project-glass) (non-usable interface)
- [Cantina Creative — ProVideoCoalition](https://www.provideocoalition.com/cantina-creative-on-designing-huds-for-movies-and-real-life/) (2D-dynamiek > dimensionaliteit)
- [HolographicMaterialVanilla — ektogamat](https://github.com/ektogamat/threejs-vanilla-holographic-material) · [Three.js Journey hologram-shader](https://threejs-journey.com/lessons/hologram-shader)
- [Three.js Journey particles-morphing](https://threejs-journey.com/lessons/particles-morphing-shader) (GPU-morphing via attributes)
- [MDN — Visualizations with Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Visualizations_with_Web_Audio_API) (canonieke analyser-recepten)
- [SiriWave — kopiro](https://github.com/kopiro/siriwave) · [TextScramble — soulwire CodePen](https://codepen.io/soulwire/pen/mEMPrK)

**Caveat uit verificatie**: CSS-borders/canvas-radar/CRT-shader-claims overleefden de
adversariële check niet als geciteerde feiten — die items (cat. 2, 4, 7) zijn op
standaardtechniek gebaseerd, niet op geverifieerde bronnen. Eén claim gesneuveld
(scanlines uit model-position i.p.v. UV's — niet overnemen).
