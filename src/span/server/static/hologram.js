/* SPAN brein-hologram: Neo4j-graph als draaiend 3D-hologram (3d-force-graph).
   Klein paneel rechts; klik ⛶ voor fullscreen. Ververst na elke beurt.
   Klik een node -> hij wordt vastgezet (pin), camera vliegt erheen en een
   blijvend infopaneel toont type, herkomst (provenance) en relaties. */
"use strict";
(() => {
  const _breinPaneel = document.getElementById("panel-brein");
  if (_breinPaneel && _breinPaneel.classList.contains("hidden")) return; // paneel uit -> niet renderen
  const SPAN = window.SPAN;
  if (typeof ForceGraph3D === "undefined") return; // vendor-bundle niet geladen
  const esc = SPAN.esc || ((s) => String(s || "").replace(/[<>&]/g,
    (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c])));

  // kleurtaal: cyaan-familie voor het 'denkweefsel', warm alleen voor wat
  // aandacht vraagt (Idea/Quest/Mistake). Session valt bewust weg in de diepte.
  const COLORS = {
    Identity: "#ffffff", MemoryFragment: "#00d4ff", Insight: "#7dffb4",
    Mistake: "#ff6b6b", Idea: "#ffd27d", Quest: "#ff9d5c", QuestStep: "#9a6b3f",
    Skill: "#a06bff", Protocol: "#38e1ff", Session: "#274a63", Entity: "#ff8ad8",
    Meeting: "#5cd6c0", Document: "#9fcf86",
  };
  const SIZES = { Identity: 14, Protocol: 7, Quest: 7, Skill: 7, Insight: 6 };
  const DIM_COLOR = "#22323c";

  let highlighted = new Map();   // key -> level (0=touched wit, 1=buur, 2=buur-van-buur)
  let adjacency = new Map();     // key -> Set(buur-keys)
  let nodeByKey = new Map();     // key -> node-object (voor relaties/labels)
  const pinned = new Set();      // vastgezette node-keys
  let hoverSet = new Set();      // node + buren onder de muis
  let controls = null;
  // live leescascade: nodes die Span TIJDENS de beurt raadpleegt lichten op en
  // doven per stuk op eigen klok -> je ziet het brein de denkroute aflopen
  let reading = new Map();       // key -> { t0, reason }
  let decayTimer = null, firstFlyDone = false, reasonEl = null;
  const DECAY_MS = 5000, READING_CAP = 25;

  function lerpColor(a, b, t) {
    const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
    const ar = (pa >> 16) & 255, ag = (pa >> 8) & 255, ab = pa & 255;
    const r = Math.round(ar + (((pb >> 16) & 255) - ar) * t);
    const g = Math.round(ag + (((pb >> 8) & 255) - ag) * t);
    const bl = Math.round(ab + ((pb & 255) - ab) * t);
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + bl).toString(16).slice(1);
  }

  const panel = document.getElementById("holo-canvas");
  const overlay = document.getElementById("holo-overlay");
  const full = document.getElementById("holo-full");
  const scene = document.getElementById("holo-scene");
  let graph = null, spinTimer = null, infoCard = null;

  function degree(key) { return adjacency.get(key) ? adjacency.get(key).size : 0; }

  // prioriteit: pinned > reading (live lees) > highlighted > hover-dim > basis
  function nodeColorFn(n) {
    if (pinned.has(n.key)) return "#ffffff";
    const rd = reading.get(n.key);
    if (rd) {
      const f = Math.max(0, 1 - (Date.now() - rd.t0) / DECAY_MS);  // 1=vers .. 0=oud
      return lerpColor(COLORS[n.type] || "#5f7a8e", "#ffffff", f);  // dooft terug naar eigen kleur
    }
    const lvl = highlighted.get(n.key);
    if (lvl === 0) return "#ffffff";
    if (lvl === 1) return "#aef2ff";
    if (lvl === 2) return "#5fd8f0";
    if (hoverSet.size && !hoverSet.has(n.key)) return DIM_COLOR;  // dim de rest
    return COLORS[n.type] || "#5f7a8e";
  }
  function nodeValFn(n) {
    if (pinned.has(n.key)) return 16;
    const rd = reading.get(n.key);
    if (rd) {
      const age = Date.now() - rd.t0, f = Math.max(0, 1 - age / DECAY_MS);
      const puls = age < 600 ? 2 * Math.abs(Math.sin(age / 90)) : 0;  // verse node ademt
      return (SIZES[n.type] || 3) + f * 8 + puls;
    }
    const lvl = highlighted.get(n.key);
    if (lvl === 0) return 12;
    if (lvl === 1) return 7;
    if (hoverSet.size && !hoverSet.has(n.key)) return 2;
    return (SIZES[n.type] || 3) + Math.min(degree(n.key) * 0.8, 10);  // belang = grootte
  }
  function linkColorFn(l) {
    const a = l.source.key || l.source, b = l.target.key || l.target;
    if (reading.has(a) || reading.has(b)) return "#bfefff";   // cascade verbindt de lees-route
    if (hoverSet.size) return (hoverSet.has(a) && hoverSet.has(b)) ? "#7fe9ff" : "#1d3540";
    if (highlighted.has(a) || highlighted.has(b)) return "#7fe9ff";
    return "#3f7d99";   // duidelijk zichtbare relatie-lijn op de donkere achtergrond
  }

  function makeGraph(el) {
    const g = ForceGraph3D({ controlType: "orbit" })(el)
      .backgroundColor("rgba(0,0,0,0)")
      .showNavInfo(false)
      .nodeRelSize(4)                       // groter klikdoel + leesbaarder
      .nodeColor(nodeColorFn)
      .nodeVal(nodeValFn)
      .nodeOpacity(0.92)
      .nodeLabel((n) => `<div class="holo-tip"><b>${esc(n.type)}</b><br>${esc(n.label || "")}</div>`)
      .linkColor(linkColorFn)
      .linkOpacity(0.7)
      .linkWidth((l) => {
        const a = l.source.key || l.source, b = l.target.key || l.target;
        return (hoverSet.has(a) && hoverSet.has(b)) ? 1.6 : 1.0;
      })
      .enableNodeDrag(false)
      .warmupTicks(40)
      .cooldownTicks(120)
      .onNodeHover((n) => {
        hoverSet = n ? new Set([n.key, ...(adjacency.get(n.key) || [])]) : new Set();
        scene.style.cursor = n ? "pointer" : "";
        updateSpin();
        repaint();
      })
      .onNodeClick(onNodeClick)
      .onBackgroundClick(() => { closeInfo(); updateSpin(); });

    // GEEN fog: de losse window.THREE is een ándere instance dan die in de
    // 3d-force-graph-bundle. Een FogExp2 van de verkeerde instance liet de
    // renderer crashen (refreshFogUniforms: "n.color.getRGB is not a function"),
    // waardoor de nodes nooit tekenden en je een leeg vierkant overhield.

    // force-tuning: verbonden nodes dichter bij elkaar -> duidelijke clusters
    // (fragmenten rond hun Document/Session) i.p.v. een gelijkmatige wolk
    try {
      const lf = g.d3Force("link"); if (lf) lf.distance(22).strength(0.4);
      const ch = g.d3Force("charge"); if (ch) ch.strength(-26);
    } catch (e) { /* force-API afwezig -> default layout */ }

    controls = g.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;
    clearInterval(spinTimer);
    spinTimer = setInterval(() =>
      controls && !document.hidden && controls.update(), 50);
    return g;
  }

  // auto-rotatie staat uit zodra je iets gepind hebt of over een node hovert
  function updateSpin() {
    if (controls) controls.autoRotate = pinned.size === 0 && hoverSet.size === 0;
  }

  function onNodeClick(n) {
    if (pinned.has(n.key)) {              // tweede klik op gepinde node -> los
      n.fx = n.fy = n.fz = undefined;
      pinned.delete(n.key);
      closeInfo();
      updateSpin();
      if (graph) graph.refresh();
      repaint();
      return;
    }
    n.fx = n.x; n.fy = n.y; n.fz = n.z;   // fysiek vastzetten
    pinned.add(n.key);
    updateSpin();
    repaint();
    // camera vliegt naar de node
    if (n.x !== undefined) {
      const d = 180, len = Math.hypot(n.x, n.y, n.z) || 1, r = 1 + d / len;
      graph.cameraPosition({ x: n.x * r, y: n.y * r, z: n.z * r }, n, 800);
    }
    showInfo(n, null);                    // meteen tonen, daarna verrijken
    if (n.key && SPAN.authHeaders) {
      fetch("/api/provenance/" + encodeURIComponent(n.key), { headers: SPAN.authHeaders() })
        .then((res) => res.ok ? res.json() : null)
        .then((p) => { if (p && p.found) showInfo(n, p); })
        .catch(() => {});
    }
  }

  /* -- infopaneel ------------------------------------------------------- */
  function ensureCard() {
    if (infoCard) return infoCard;
    infoCard = document.createElement("div");
    infoCard.id = "holo-info";
    infoCard.className = "holo-card";
    (overlay.classList.contains("open") ? full : panel).appendChild(infoCard);
    return infoCard;
  }
  function closeInfo() { if (infoCard) infoCard.classList.remove("open"); }

  function showInfo(n, p) {
    const card = ensureCard();
    const rels = [...(adjacency.get(n.key) || [])].slice(0, 8).map((k) => {
      const nb = nodeByKey.get(k);
      return nb ? `<div class="holo-rel"><i>${esc(nb.type)}</i> ${esc(nb.label || "")}</div>` : "";
    }).filter(Boolean);
    const src = [];
    if (p && p.found) {
      if (p.session && p.session.summary) src.push("uit sessie: " + esc(p.session.summary.slice(0, 100)));
      if (p.sources && p.sources.length) src.push("gedestilleerd uit " + p.sources.length + " fragment(en)");
      if (p.entities && p.entities.length) src.push("noemt: " + esc(p.entities.join(", ")));
    }
    const SHAREABLE = ["Insight", "Mistake", "Idea", "Skill", "Protocol", "MemoryFragment"];
    const canShare = SHAREABLE.includes(n.type);
    card.innerHTML =
      `<button class="iconbtn holo-x" title="Sluiten" aria-label="Sluiten">✕</button>` +
      `<h3>${esc(n.type)}</h3>` +
      `<div class="holo-title">${esc(n.label || "")}</div>` +
      (src.length ? `<h4>herkomst</h4>` + src.map((s) => `<div class="holo-src">${s}</div>`).join("") : "") +
      (rels.length ? `<h4>relaties</h4>` + rels.join("") : "") +
      (canShare ? `<button class="holo-share" title="Kopieer naar het gedeelde team-geheugen">⇪ deel met team</button>` : "") +
      `<div class="holo-hint">klik de node opnieuw om los te maken</div>`;
    card.querySelector(".holo-x").onclick = () => { closeInfo(); };
    const sb = card.querySelector(".holo-share");
    if (sb) sb.onclick = () => {
      sb.disabled = true; sb.textContent = "delen…";
      fetch("/api/share", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" },
                               SPAN.authHeaders ? SPAN.authHeaders() : {}),
        body: JSON.stringify({ id: n.key }),
      })
        .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
        .then(({ ok, d }) => { sb.textContent = ok ? "✓ gedeeld met team" : ("✗ " + (d.detail || "mislukt")); })
        .catch(() => { sb.textContent = "✗ fout"; sb.disabled = false; });
    };
    card.classList.add("open");
  }

  function size() {
    const r = scene.parentElement.getBoundingClientRect();
    if (graph) graph.width(r.width).height(r.height);
  }
  addEventListener("resize", size);
  // auto-rotate pauzeren bij hover op het hele paneel (spaart frames op ARM64)
  if (panel) {
    panel.addEventListener("pointerenter", () => { if (controls) controls.autoRotate = false; });
    panel.addEventListener("pointerleave", () => updateSpin());
  }

  async function load() {
    try {
      const days = SPAN._holoSince || 0;
      const url = "/api/graph?limit=250" + (days ? "&since=" + days : "");
      const res = await fetch(url, { headers: SPAN.authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      if (!graph) { graph = makeGraph(scene); size(); }
      else flicker();
      graph.graphData(data);
      nodeByKey = new Map(data.nodes.map((n) => [n.key, n]));
      // adjacency op key-basis
      const byId = new Map(data.nodes.map((n) => [n.id, n.key]));
      adjacency = new Map();
      for (const l of data.links) {
        const a = byId.get(l.source.id ?? l.source), b = byId.get(l.target.id ?? l.target);
        if (!a || !b) continue;
        if (!adjacency.has(a)) adjacency.set(a, new Set());
        if (!adjacency.has(b)) adjacency.set(b, new Set());
        adjacency.get(a).add(b); adjacency.get(b).add(a);
      }
      // gepinde nodes blijven vastgezet na een refresh (nieuwe node-objecten)
      for (const n of data.nodes) {
        if (pinned.has(n.key)) { n.fx = n.x; n.fy = n.y; n.fz = n.z; }
      }
      const el = document.getElementById("holo-count");
      if (el) el.textContent = `${data.nodes.length} nodes · ${data.links.length} relaties`;
    } catch (e) { /* stil */ }
  }

  function flicker() {
    const el = scene;
    el.style.transition = "none"; el.style.opacity = ".35";
    setTimeout(() => { el.style.transition = "opacity .12s"; el.style.opacity = "1"; }, 60);
    setTimeout(() => { el.style.opacity = ".6"; }, 220);
    setTimeout(() => { el.style.opacity = "1"; }, 300);
  }

  function repaint() {
    if (!graph) return;
    graph.nodeColor(nodeColorFn).nodeVal(nodeValFn)
         .linkColor(linkColorFn);
  }

  /* -- live leescascade: Span 'denkt zichtbaar' ------------------------- */
  function flyTo(key) {
    const node = graph && graph.graphData().nodes.find((n) => n.key === key);
    if (node && node.x !== undefined) {
      const d = 200, len = Math.hypot(node.x, node.y, node.z) || 1, r = 1 + d / len;
      graph.cameraPosition({ x: node.x * r, y: node.y * r, z: node.z * r }, node, 800);
    }
  }
  function setReason(reason) {
    if (!reasonEl) return;
    reasonEl.textContent = reason ? "leest: " + reason : "";
    reasonEl.style.opacity = reason ? "1" : "0";
  }
  function decayTick() {
    const now = Date.now();
    let alive = false;
    for (const [k, s] of reading) {
      if (now - s.t0 > DECAY_MS) reading.delete(k); else alive = true;
    }
    repaint();
    if (!alive) {
      setReason("");
      if (controls) controls.autoRotateSpeed = 0.5;   // terug uit 'denk-modus'
      clearInterval(decayTimer); decayTimer = null;
    }
  }
  // door de WS-laag aangeroepen zodra Span een herinnering raadpleegt
  SPAN.markReading = (ids, reason) => {
    if (!graph || !ids || !ids.length) return;
    const now = Date.now();
    for (const k of ids) reading.set(k, { t0: now, reason });
    if (reading.size > READING_CAP) {  // oudste sporen eruit -> framerate-vangnet
      [...reading.entries()].sort((a, b) => a[1].t0 - b[1].t0)
        .slice(0, reading.size - READING_CAP).forEach(([k]) => reading.delete(k));
    }
    setReason(reason);
    if (controls) controls.autoRotateSpeed = 0.2;       // rustiger draaien tijdens 't denken
    if (!firstFlyDone) { firstFlyDone = true; flyTo(ids[0]); }
    if (!decayTimer) decayTimer = setInterval(decayTick, 120);
    repaint();
  };
  // per beurt resetten zodat de camera maar één keer naar de eerste lees vliegt
  SPAN.beginTurn = () => { firstFlyDone = false; };

  /* volgmodus + pulse-propagatie: touched licht wit op, de puls reist 2 hops */
  let highlightTimer = null;
  SPAN.highlightNodes = (ids) => {
    if (!graph || !ids.length) return;
    highlighted = new Map(ids.map((k) => [k, 0]));
    repaint();
    const node = graph.graphData().nodes.find((n) => n.key === ids[0]);
    if (node && node.x !== undefined) {
      const d = 220, len = Math.hypot(node.x, node.y, node.z) || 1;
      graph.cameraPosition(
        { x: node.x * (1 + d / len), y: node.y * (1 + d / len), z: node.z * (1 + d / len) },
        node, 800);
    }
    setTimeout(() => {
      for (const k of ids) for (const nb of adjacency.get(k) || [])
        if (!highlighted.has(nb)) highlighted.set(nb, 1);
      repaint();
    }, 450);
    setTimeout(() => {
      const lvl1 = [...highlighted.entries()].filter(([, l]) => l === 1).map(([k]) => k);
      for (const k of lvl1) for (const nb of adjacency.get(k) || [])
        if (!highlighted.has(nb)) highlighted.set(nb, 2);
      repaint();
    }, 900);
    clearTimeout(highlightTimer);
    highlightTimer = setTimeout(() => { highlighted = new Map(); repaint(); }, 9000);
  };

  /* throttled refresh na elke beurt */
  let lastRefresh = 0;
  SPAN.refreshHologram = () => {
    const now = Date.now();
    if (now - lastRefresh < 15000) return;
    lastRefresh = now;
    load();
  };

  /* fullscreen toggle: scene + infopaneel + bediening (zoek/filters) mee */
  function toggleFull() {
    const goingFull = !overlay.classList.contains("open");
    overlay.classList.toggle("open", goingFull);
    const section = panel.parentElement;                 // #panel-brein
    const count = document.getElementById("holo-count");
    const sWrap = document.getElementById("holo-search-wrap");
    const fBox = document.getElementById("holo-filters");
    if (goingFull) {
      full.appendChild(scene);
      if (sWrap) full.appendChild(sWrap);
      if (fBox) full.appendChild(fBox);
      if (infoCard) full.appendChild(infoCard);
    } else {
      panel.appendChild(scene);                          // panel = #holo-canvas
      if (sWrap) section.insertBefore(sWrap, count);      // oorspronkelijke volgorde
      if (fBox) section.insertBefore(fBox, count);
      if (infoCard) panel.appendChild(infoCard);
    }
    size();
    if (graph) graph.zoomToFit(600, 60);
  }
  document.getElementById("holo-expand").onclick = toggleFull;
  document.getElementById("holo-close").onclick = toggleFull;

  /* zoeken: spring naar een node */
  const searchWrap = document.getElementById("holo-search-wrap");
  if (searchWrap) {
    const input = document.createElement("input");
    input.id = "holo-search"; input.placeholder = "zoek node…"; input.autocomplete = "off";
    const sug = document.createElement("div"); sug.id = "holo-suggest";
    reasonEl = document.createElement("div"); reasonEl.id = "holo-reason";
    searchWrap.append(input, sug, reasonEl);
    const pick = (n) => { sug.innerHTML = ""; input.value = ""; SPAN.highlightNodes([n.key]); };
    input.oninput = () => {
      const q = input.value.trim().toLowerCase();
      sug.innerHTML = "";
      if (q.length < 2 || !graph) return;
      const hits = graph.graphData().nodes
        .filter((n) => (n.label || "").toLowerCase().includes(q)).slice(0, 6);
      for (const n of hits) {
        const b = document.createElement("button");
        b.className = "holo-sug"; b.textContent = `${n.type} · ${n.label || ""}`.slice(0, 48);
        b.onclick = () => pick(n);
        sug.appendChild(b);
      }
    };
  }

  /* type-filters: chips */
  const hiddenTypes = new Set();
  const FILTERS = [["MF", "MemoryFragment"], ["entiteit", "Entity"], ["inzicht", "Insight"],
    ["quest", "Quest"], ["sessie", "Session"], ["protocol", "Protocol"], ["doc", "Document"]];
  const filterBox = document.getElementById("holo-filters");
  if (filterBox) {
    for (const [label, type] of FILTERS) {
      const chip = document.createElement("button");
      chip.className = "holo-chip on";
      chip.textContent = label;
      chip.style.borderColor = COLORS[type] || "#5f7a8e";
      chip.onclick = () => {
        if (hiddenTypes.has(type)) hiddenTypes.delete(type); else hiddenTypes.add(type);
        chip.classList.toggle("on", !hiddenTypes.has(type));
        if (graph) graph.nodeVisibility((n) => !hiddenTypes.has(n.type));
      };
      filterBox.appendChild(chip);
    }
    // tijdvenster-knoppen (250-cap leesbaar houden bij een groeiend brein)
    for (const [label, days] of [["7d", 7], ["30d", 30], ["alles", 0]]) {
      const chip = document.createElement("button");
      chip.className = "holo-chip win" + (days === 0 ? " on" : "");
      chip.textContent = label;
      chip.onclick = () => {
        SPAN._holoSince = days;
        filterBox.querySelectorAll(".holo-chip.win").forEach((c) => c.classList.remove("on"));
        chip.classList.add("on");
        lastRefresh = 0; load();
      };
      filterBox.appendChild(chip);
    }
  }

  setTimeout(load, 2500);
  setInterval(() => { if (!document.hidden) load(); }, 120000);
})();
