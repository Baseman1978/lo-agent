# Werkplan — NEBULA-HUD: orb + geheugenwolk als voorpagina van LO

**Doel (Bas, 2026-07-03):** de jarvis-hud NEBULA-visualisatie (sandbox in
`C:\GitHub\jarvis-hud`) wordt het middelpunt van LO's voorpagina: de
GPGPU-orb (65k stromende deeltjes, "gezichtsuitdrukkingen" per status) als
de agent, het échte brein als wolk eromheen, cinema-postprocessing erover.
Vervangt de huidige reactor-orb en (uiteindelijk) het hologram-paneel.

## Architectuurbesluiten

1. **Zelfstandige bundel, geen versie-huwelijk.** LO's vendored Three r128 en
   jarvis-hud's Three 0.185 kunnen niet in één scene. De NEBULA wordt een
   eigen ES-bundel (`static/hud/nebula.js` + eigen canvas) met moderne Three
   erin gebundeld. CSP blijft ongewijzigd (`script-src 'self'` — alles
   zelf-gehost, geen CDN).
2. **Build-stap, maar de server blijft buildloos.** Nieuwe map `hud/` in de
   repo (Vite + TypeScript, versies gepind zoals de sandbox). `npm run build`
   schrijft naar `src/span/server/static/hud/`; de **output wordt gecommit**
   zodat Docker/deploy geen node nodig heeft. CI krijgt een check: bundel
   herbouwen → `git diff --exit-code` (dist mag nooit uit de pas lopen).
3. **Feature-flag, oude weergave blijft.** Instellingen → Uiterlijk krijgt
   "Weergave: NEBULA (nieuw) / Klassiek". Klassiek = huidige orb + hologram.
   Default blijft klassiek tot fase N4 is geaccepteerd; geen WebGL2 op het
   apparaat → automatisch klassiek.
4. **Echte data via bestaande naden.** `/api/graph` (nodes: id/type/key/label,
   links) voedt de wolk; kleuren per LO-label (13 types, mapping uit het
   huidige hologram.js). WS `memory_read` (ids + reden) → leescascade:
   oplichten, sparks over de verbindingen, camera-vlucht. Nieuwe memories →
   lichtpuls van orb naar node (drain-pattern zit al in de sandbox).
5. **Agent-status uit bestaande signalen.** idle (rust) · listening (mic/wake
   aan) · thinking (beurt bezig/tool draait) · speaking (TTS speelt) ·
   **alert** (Agent Inbox heeft open acties, of brein offline). Kleuren zoals
   de sandbox (cyan/emerald/violet/amber/rood).
6. **Instellingen (de "kick-ass"-knoppen).** Het tuning-paneel van de sandbox
   (dichtheid, lichtflitsen, aders, puls-ringen + presets Standaard/Dichte
   wolk/Flitsend) verhuist naar Instellingen → Uiterlijk, bewaard in
   localStorage. Vervangt de huidige orb-instellingen.
7. **Performance-vangrails.** Adaptieve kwaliteit uit de sandbox (fps<40 →
   pixelratio + cosmetica omlaag) blijft; plus: `document.hidden` pauzeert
   alles (Fase D-lijn), en een **lite-profiel** voor mobiel/integrated GPU
   (16k deeltjes i.p.v. 65k, geen depth-of-field). De 50k-punts
   embedding-wolk doet in v1 **niet** mee (geen echte coördinaten; scheelt
   het meeste GPU-budget).
8. **AVG.** Single-user: alle data is van Bas zelf, labels zijn al op 70
   tekens afgekapt — geen extra redactie nodig. Bij multi-user (fase 2 van
   het Community-werkplan) levert owner-scoping per gebruiker al de juiste
   deelgraaf; gevoelige klassen dimmen/redigeren is dan een serverkeuze,
   genoteerd in de AVG-docs.

## Fases (elk een eigen PR, met tests + live-verificatie)

**N0 — Build-fundament** *(~dagdeel)*
`hud/`-map met Vite/TS (gepinde versies uit de sandbox), bundel naar
`static/hud/`, CI-check "dist actueel", feature-flag + WebGL2-detectie in de
Uiterlijk-tab (standaard uit). Lege scene die alleen een canvas toont.
✔ Klaar als: flag aan → leeg NEBULA-canvas op de voorpagina, CI groen.

**N1 — Scene-port** *(~dag)*
Orb (GPGPU + aders + kern + ripples), graaf-engine, holte-kracht en
postprocessing uit de sandbox overnemen, nog op synthetische data. Adaptive
quality + hidden-pauze + lite-profiel. Layout: de NEBULA wordt het
center-achtergrondcanvas; chat-log en invoerbalk zweven eroverheen (log
krijgt een donkere waas voor leesbaarheid).
✔ Klaar als: sandbox-beeld draait ín LO op desktop én telefoon (lite).

**N2 — Echt brein** *(~dagdeel)*
`/api/graph`-adapter (label→kleur/grootte, hubs groter), refresh elke 120s
incrementeel (drain-pattern), klik op node → detailpaneel (type, label,
relaties, deel-knop — gedrag van het huidige hologram), hover-tooltip,
type/tijd-filters en zoeken uit het huidige paneel.
✔ Klaar als: de 1.199 echte nodes als wolk om de orb draaien, klik werkt.

**N3 — Levend** *(~dagdeel)*
WS-koppeling: `memory_read` → leescascade (oplichten + sparks + reden-label
+ camera-vlucht), nieuwe memories → orb-naar-node lichtpuls, agent-status
uit SPAN-signalen (incl. alert bij open inbox). Reactor-API's
(`SPAN.glitch`, `reactorOk`, audio-reactie op TTS) doorverbonden.
✔ Klaar als: een chatbeurt zichtbaar door het brein "denkt".

**N4 — Instellingen + acceptatie** *(~dag)*
Tuning-paneel in Uiterlijk-tab (schuiven + presets), fullscreen-knop,
performance-acceptatie op de echte apparaten van Bas (telefoon + laptop:
vloeiend of automatisch lite). Daarna: default naar NEBULA, klassiek blijft
kiesbaar.
✔ Klaar als: Bas hem op z'n telefoon soepel ziet draaien en 'm aanzet.

**N5 — Opruimen** *(na akkoord-periode)*
Oude orb.js + hologram-paneel + bijbehorende vendor-libs verwijderen (~1,8
MB minder), rechter kolom herindelen (mail/agenda krijgen de ruimte),
docs bijwerken.
✔ Klaar (PR #100, 2026-07-04): orb.js, hologram.js, three r128,
3d-force-graph en de bloom-shaders zijn weg (−2143 regels). Het
Brein-paneel bleef als compacte statistieken; er is geen klassieke
fallback meer (zonder WebGL2 gewoon geen 3D-scene).

## Risico's & mitigaties

| Risico | Mitigatie |
|---|---|
| GPGPU te zwaar op kantoorlaptop/telefoon | lite-profiel + adaptive quality + N4-acceptatie vóór default-aan |
| Geen WebGL2/float-textures op een apparaat | detectie → automatisch klassieke weergave |
| Twee Three-bundels tijdens overgang (~1,8 MB extra) | tijdelijk; N5 ruimt de oude op; alles lokaal geserveerd |
| Bundel-drift (dist ≠ source) | CI-check herbouwt en vergelijkt |
| Chat-leesbaarheid over de drukke scene | waas achter de log + dichtheid-schuif; N1-review door Bas |

## Open keuzes voor Bas (mogen tijdens N1/N2)

1. Vervangt de NEBULA ook het **rechter hologram-paneel** meteen (mijn
   voorstel: ja — één brein, groot in het midden), of eerst alleen de orb?
2. Embedding-puntenwolk later alsnog (vergt echte reductie-coördinaten
   server-side) — v2-kandidaat of schrappen?
3. Moment van default-aan (na N4-acceptatie).
