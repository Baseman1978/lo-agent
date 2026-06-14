# Span 3D-brein-hologramview — Bouwklaar ontwerp

Bron: multi-agent deep research (esthetiek · leesbaarheid/selectie · klik-pin+info ·
performance), ARM64-feasibility per idee getoetst. 2026-06-14.

## 1. Visie
Een rustig, ademend brein van cyaan licht: transparante 3D-graaf die zacht ronddraait
boven een gloeiende glasplaat. Belangrijke knopen (Identity/Quest/Protocol) gloeien
feller en dragen een leesbaar label; de rest valt via fog terug in de diepte. Klik je
een node → hij fixeert zich, de camera vliegt ernaartoe en een blijvend glas-infopaneel
toont type, herkomst en relaties. Vloeiend op 60fps op de GPU-loze ARM64-host: de
zwaarte blijft BUITEN WebGL (CSS-glow, géén bloom) en de render-loop is state-driven.

## 2. Mooier (ARM64-haalbaar — bestand: src/span/server/static/hologram.js, tenzij anders)
- **GEEN bloom/postprocessing** — UnrealBloomPass op SwiftShader = single-digit fps. Bewuste keuze.
- **Glow via CSS** — `box-shadow` (rand + inset) op `#holo-canvas` in jarvis.css. NIET `filter:blur/drop-shadow` op de bewegende canvas (per-frame CPU-blur).
- **Sprite-halo per node** — gebakken 64×64 radiale-gradient `CanvasTexture` → `THREE.Sprite` (AdditiveBlending, depthWrite:false), alleen op highlight-0/1 + grote types. THREE uit de bundle halen.
- **Emissive-look via kleurluminantie** — achtergrond-nodes donkerder, Insight/Idea/Quest fel (nodeOpacity is globaal, dus via kleur niet opacity).
- **Link-zwaarte** — `linkWidth/linkColor` op het `rel`-veld (bestaat al in /api/graph); default dun/donker.
- **Depth-fog** — `scene.fog = new THREE.FogExp2(0x04070e, 0.0018)`; `fog:false` op halo's.
- **CSS grid + fresnel-vignette** — `::before` op `#holo-canvas` (repeating-linear-gradient + radiale vignette). CSP-proof.
- **Kleurtaal-verfijning** — COLORS-map herijken op jarvis.css-tokens; warm alleen voor Idea/Quest/Mistake.

## 3. Leesbaarder & selecteerbaarder
- **Altijd-zichtbare labels (voorwaardelijk)** — `three-spritetext` lokaal vendoren (CSP), `nodeThreeObjectExtend(true)`; **hard cappen op ~12-20 nodes** (Identity + top-N op degree), NIET alle 250 (textures haperen op SwiftShader). Niet elke refresh herbouwen.
- **Node-grootte naar belang** — `nodeVal` met degree-bonus uit `adjacency`.
- **Hover → buurt oplichten + rest dimmen** — aparte `hoverSet`, `onNodeHover`, dim niet-buren; autoRotate uit tijdens hover.
- **Klikdoel groter** — `nodeRelSize` 3→4-5, cursor pointer, autoRotate uit bij pointerenter (spaart frames).
- **Zoeken/spring-naar-node** — `<input>` bij de filter-chips → bestaande `SPAN.highlightNodes` (vliegt camera).
- **250-cap leesbaar** — `/api/graph` `since`-param (dagen) + formele labels altijd; segmented control 7d/30d/alles.

## 4. Klik-om-te-pinnen + infopaneel (hologram.js .onNodeClick r.46 + jarvis.css)
1. **Pinnen** — `pinned`-Set; klik togglet `n.fx/fy/fz` = positie (of undefined); fx/fy/fz herstellen na de 120s-`load()`.
2. **Camera-fly + autoRotate uit** — `cameraPosition(...,n,800)`; autoRotate uit zolang ≥1 pin.
3. **Gepinde markering — accessor-variant** (NIET RingGeometry: bestaat niet in de afgeslankte bundle-THREE → crash): pinned → wit + grotere `nodeVal` via `repaint()`. 0 extra draw calls.
4. **Blijvend infopaneel** — één `div#holo-info.holo-card`; vul met type/label + provenance (`/api/provenance` bestaat al) + buren uit `adjacency`. Vervangt de vluchtige `SPAN.sys`. Escape met bestaande `<`-replace.
5. **Los-pinnen** — `onBackgroundClick` sluit paneel + autoRotate aan; nogmaals klikken op node = unpin (multi-pin blijft mogelijk).
- Paneel = glas-kaart (`backdrop-filter:blur(6px)` op klein statisch paneel = goedkoop; fullscreen-fallback effen).

## 5. Performance (dirty-flag op ARM64)
Grondoorzaak hapering = blinde `setInterval(controls.update,40)` → ~25 forced renders/s.
- **State-driven render** — `onEngineStop` → `pauseAnimation()` als sim uitgekoeld én geen rotatie; `resumeAnimation()` bij klik/hover/highlight/toggle/load/resize (+800ms voor camera-fly).
- **Auto-rotate alleen in beeld** — `IntersectionObserver` + `visibilitychange`.
- **Base-render budget** — spin-interval 40→60-80ms, lagere DPR, `antialias:false`, kleinere node/link-maten.
- **Particles** — photons zijn niet-geïnstanceerd (duur). 'Datastroom' liever via geanimeerde `linkColor`-puls (0 draw calls); echte `linkDirectionalParticles` alleen tijdelijk tijdens de 9s-puls, gecapt, na fps-meting.

## 6. Implementatieplan (geprioriteerd)
**Fase A — Quick wins (S), pure waarden/CSS:** 1) CSS rand-glow+grid+vignette · 2) kleurtaal+emissive · 3) node-grootte naar degree · 4) klikdoel groter+autoRotate-uit · 5) node pinnen+camera-fly · 6) zoek-input · 7) infopaneel-CSS · 8) depth-fog.
**Fase B — Middel (M):** 9) blijvend infopaneel (DOM+provenance+relaties) · 10) los-pinnen+gepinde markering · 11) hover-buurt · 12) link-zwaarte · 13) dirty-flag render · 14) `since`-filter (routes.py) · 15) sprite-halo.
**Fase C — Voorwaardelijk, meet fps eerst:** 16) three-spritetext labels (cap ≤20) · 17) label-LOD · 18) screen-blend overlay · 19) link-kleurpuls/particles.
Stappen 1-14 = laag risico, direct. 15-19 = fps-meting in Docker/SwiftShader vóór default-aan.

## 7. Bronnen (kern)
- 3d-force-graph: https://github.com/vasturiano/3d-force-graph
- three-forcegraph (photons per-Mesh): https://github.com/vasturiano/three-forcegraph
- three-spritetext: https://github.com/vasturiano/three-spritetext
- FogExp2: https://threejs.org/docs/pages/FogExp2.html
- SwiftShader (software-WebGL): https://github.com/google/swiftshader
- three.js best practices (<100 draw calls): https://utsubo.com/blog/threejs-best-practices-100-tips
- Shneiderman overview→zoom→detail: https://infovis-wiki.net/wiki/Visual_Information-Seeking_Mantra
