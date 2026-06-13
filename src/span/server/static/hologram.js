/* SPAN brein-hologram: Neo4j-graph als draaiend 3D-hologram (3d-force-graph).
   Klein paneel rechts; klik ⛶ voor fullscreen. Ververst na elke beurt. */
"use strict";
(() => {
  const SPAN = window.SPAN;
  if (typeof ForceGraph3D === "undefined") return; // vendor-bundle niet geladen

  const COLORS = {
    Identity: "#ffffff", MemoryFragment: "#00d4ff", Insight: "#7dffb4",
    Mistake: "#ff6b6b", Idea: "#ffd27d", Quest: "#ff9d5c", QuestStep: "#9a6b3f",
    Skill: "#a06bff", Protocol: "#38e1ff", Session: "#2a5a78", Entity: "#ff8ad8",
    Meeting: "#5cd6c0", Document: "#c8e66e",
  };
  let highlighted = new Map();   // key → level (0=touched wit, 1=buur, 2=buur-van-buur)
  let adjacency = new Map();     // key → Set(buur-keys), voor pulse-propagatie
  const SIZES = { Identity: 14, Protocol: 7, Quest: 7, Skill: 7, Insight: 6 };

  const panel = document.getElementById("holo-canvas");
  const overlay = document.getElementById("holo-overlay");
  const full = document.getElementById("holo-full");
  const scene = document.getElementById("holo-scene");
  let graph = null, spinTimer = null;

  function makeGraph(el) {
    const g = ForceGraph3D({ controlType: "orbit" })(el)
      .backgroundColor("rgba(0,0,0,0)")
      .showNavInfo(false)
      .nodeRelSize(3)
      .nodeColor((n) => {
        const lvl = highlighted.get(n.key);
        return lvl === 0 ? "#ffffff" : lvl === 1 ? "#aef2ff"
          : lvl === 2 ? "#5fd8f0" : (COLORS[n.type] || "#5f7a8e");
      })
      .nodeVal((n) => {
        const lvl = highlighted.get(n.key);
        return lvl === 0 ? 10 : lvl === 1 ? 6 : (SIZES[n.type] || 3);
      })
      .nodeOpacity(0.92)
      .nodeLabel((n) => `<div class="holo-tip"><b>${n.type}</b><br>${(n.label || "").replace(/</g, "&lt;")}</div>`)
      .linkColor(() => "#1a3a4a")
      .linkOpacity(0.45)
      .linkWidth(0.6)
      .enableNodeDrag(false)
      .warmupTicks(40)
      .cooldownTicks(120)
      .onNodeClick((n) => {
        SPAN.sys(`[${n.type}] ${n.label}`);
        // F3.5 — 'waarom weet je dit?': haal de bron-keten op en toon hem
        if (n.key && SPAN.authHeaders) {
          fetch("/api/provenance/" + encodeURIComponent(n.key),
                { headers: SPAN.authHeaders() })
            .then((r) => r.ok ? r.json() : null)
            .then((p) => {
              if (!p || !p.found) return;
              const bits = [];
              if (p.session && p.session.summary)
                bits.push("uit sessie: " + p.session.summary.slice(0, 80));
              if (p.sources && p.sources.length)
                bits.push("gedestilleerd uit " + p.sources.length + " fragment(en)");
              if (p.entities && p.entities.length)
                bits.push("noemt: " + p.entities.join(", "));
              if (bits.length) SPAN.sys("↳ herkomst — " + bits.join(" · "));
            })
            .catch(() => {});
        }
      });
    // langzame cinematic auto-rotatie
    const controls = g.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.55;
    clearInterval(spinTimer);
    spinTimer = setInterval(() => controls.update(), 40);
    return g;
  }

  function size() {
    const r = scene.parentElement.getBoundingClientRect();
    if (graph) graph.width(r.width).height(r.height);
  }
  addEventListener("resize", size);

  async function load() {
    try {
      const res = await fetch("/api/graph?limit=250", { headers: SPAN.authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      if (!graph) { graph = makeGraph(scene); size(); }
      else flicker();  // #54: projectie-hapering bij refresh
      graph.graphData(data);
      // adjacency op key-basis voor pulse-propagatie (#46)
      const byId = new Map(data.nodes.map((n) => [n.id, n.key]));
      adjacency = new Map();
      for (const l of data.links) {
        const a = byId.get(l.source.id ?? l.source), b = byId.get(l.target.id ?? l.target);
        if (!a || !b) continue;
        if (!adjacency.has(a)) adjacency.set(a, new Set());
        if (!adjacency.has(b)) adjacency.set(b, new Set());
        adjacency.get(a).add(b); adjacency.get(b).add(a);
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
    graph.nodeColor(graph.nodeColor());
    graph.nodeVal(graph.nodeVal());
  }

  /* volgmodus + pulse-propagatie (#46): touched licht wit op, de puls
     reist 2 hops door de relaties — denken wordt letterlijk zichtbaar */
  let highlightTimer = null;
  SPAN.highlightNodes = (ids) => {
    if (!graph || !ids.length) return;
    highlighted = new Map(ids.map((k) => [k, 0]));
    repaint();
    // camera vliegt naar de eerste touched node (#48)
    const node = graph.graphData().nodes.find((n) => n.key === ids[0]);
    if (node && node.x !== undefined) {
      const d = 220;
      const len = Math.hypot(node.x, node.y, node.z) || 1;
      graph.cameraPosition(
        { x: node.x * (1 + d / len), y: node.y * (1 + d / len), z: node.z * (1 + d / len) },
        node, 800,
      );
    }
    setTimeout(() => {  // hop 1
      for (const k of ids) for (const nb of adjacency.get(k) || []) {
        if (!highlighted.has(nb)) highlighted.set(nb, 1);
      }
      repaint();
    }, 450);
    setTimeout(() => {  // hop 2
      const lvl1 = [...highlighted.entries()].filter(([, l]) => l === 1).map(([k]) => k);
      for (const k of lvl1) for (const nb of adjacency.get(k) || []) {
        if (!highlighted.has(nb)) highlighted.set(nb, 2);
      }
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

  /* fullscreen toggle: de scene-div (met renderer) verhuist mee */
  function toggleFull() {
    const goingFull = !overlay.classList.contains("open");
    overlay.classList.toggle("open", goingFull);
    (goingFull ? full : panel).appendChild(scene);
    size();
    if (graph) graph.zoomToFit(600, 60);
  }
  document.getElementById("holo-expand").onclick = toggleFull;
  document.getElementById("holo-close").onclick = toggleFull;

  /* type-filters: chips onder het paneel */
  const hiddenTypes = new Set();
  const FILTERS = [["MF", "MemoryFragment"], ["entiteit", "Entity"], ["inzicht", "Insight"],
    ["quest", "Quest"], ["sessie", "Session"], ["protocol", "Protocol"]];
  const filterBox = document.getElementById("holo-filters");
  if (filterBox) {
    for (const [label, type] of FILTERS) {
      const chip = document.createElement("button");
      chip.className = "holo-chip on";
      chip.textContent = label;
      chip.style.borderColor = COLORS[type] || "#5f7a8e";
      chip.onclick = () => {
        if (hiddenTypes.has(type)) hiddenTypes.delete(type);
        else hiddenTypes.add(type);
        chip.classList.toggle("on", !hiddenTypes.has(type));
        if (graph) graph.nodeVisibility((n) => !hiddenTypes.has(n.type));
      };
      filterBox.appendChild(chip);
    }
  }

  /* eerste load zodra boot voorbij is */
  setTimeout(load, 2500);
  setInterval(load, 120000);
})();
