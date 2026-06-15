/* SPAN centrale orb — dcyoung-sphere port (MIT-idee): ~honderden kubusjes op
   een Fibonacci-bol die radiaal pulseren op SPAN.state + SPAN.micLevel.
   Vervangt de klassieke arc-reactor (fx.js) als SPAN._orbActive=true.
   Live instelbaar via SPAN.applyOrbConfig() (instellingen -> Orb-tab). */
"use strict";
(() => {
  const SPAN = (window.SPAN = window.SPAN || {});
  const wrap = document.getElementById("reactor-wrap");
  const classic = document.getElementById("reactor");
  if (!wrap || !classic) return;

  const PALETTES = {
    span:   ["#10204f","#1f7fae","#38e1ff","#bdf3ff","#ffffff","#ffe2a6","#ff9d5c"],
    ijs:    ["#06243a","#0e5a8a","#39b6ff","#a7e8ff","#ffffff"],
    vuur:   ["#1a0500","#7a1500","#ff5a1e","#ffb000","#ffe98a","#ffffff"],
    paars:  ["#1a0936","#5a1e9a","#a06bff","#e08aff","#ffd6ff","#ffffff"],
    regenboog: ["#ff004c","#ff9d00","#fff200","#22e36b","#19b6ff","#7a4bff","#ff2bd6"],
    cyaan:  ["#031b27","#0a4f6b","#38e1ff","#cffaff","#ffffff"],
    cooltowarm: ["#3b4cc0","#7b9ff9","#c0d4f5","#f2cbb7","#ee8468","#b40426"],
    zonsondergang: ["#0d1b3e","#3b2f63","#9a3b8f","#ff6b6b","#ffb347","#ffe9a8"],
    natuur: ["#04231a","#0a5a3c","#33b06a","#a7e8b0","#f0ffe0"],
    goud:   ["#1a1200","#5a3b00","#c8920f","#ffd27d","#fff2c8","#ffffff"],
  };
  const DEFAULTS = { style:"orb", shape:"bol", cubes:600, pulse:1.0, rotation:1.0,
                     cubeSize:0.05, radius:2.0, palette:"span", smooth:0.25 };

  function load() {
    try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem("span_orb") || "{}")); }
    catch (e) { return Object.assign({}, DEFAULTS); }
  }
  let cfg = load();

  // three.js aanwezig? zo niet: orb uit, klassieke reactor blijft draaien
  const hasTHREE = typeof THREE !== "undefined";
  SPAN._orbActive = false;

  let renderer, scene, cam, mesh, geo, mat, cv, pts = [], NB = 48, bars = null;
  const m4 = (hasTHREE ? new THREE.Matrix4() : null);
  let tint = null, tintAmt = 0, flare = 0, rot = 0.12, t = 0, raf = 0;

  function hex(h){return [parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)];}
  function paletteColor(stops, x){
    const R = stops.map(hex); x = Math.max(0, Math.min(1, x));
    const f = x*(R.length-1), i = Math.floor(f), k = f-i;
    const a = R[i], b = R[Math.min(i+1,R.length-1)];
    return new THREE.Color((a[0]+(b[0]-a[0])*k)/255,(a[1]+(b[1]-a[1])*k)/255,(a[2]+(b[2]-a[2])*k)/255);
  }

  function buildCanvas() {
    cv = document.createElement("canvas");
    cv.id = "orb-canvas";
    cv.style.cssText = "position:absolute;top:2px;left:50%;transform:translateX(-50%);" +
      "width:190px;height:190px;pointer-events:none;" +
      "filter:drop-shadow(0 0 26px rgba(56,225,255,.35))";
    wrap.insertBefore(cv, classic.nextSibling);
    renderer = new THREE.WebGLRenderer({ canvas: cv, alpha: true, antialias: true });
    renderer.setPixelRatio(1); renderer.setSize(300, 300, false);
    scene = new THREE.Scene();
    cam = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    tint = new THREE.Color(0x38e1ff);
  }

  // (her)bouw de bol bij gewijzigd aantal kubussen / palet / grootte / straal
  function buildMesh() {
    if (mesh) { scene.remove(mesh); geo.dispose(); mat.dispose(); }
    const N = Math.max(120, Math.min(1200, cfg.cubes | 0));
    cam.position.z = cfg.radius * 3;
    geo = new THREE.BoxGeometry(cfg.cubeSize, cfg.cubeSize, cfg.cubeSize);
    mat = new THREE.MeshBasicMaterial({ toneMapped: false });
    mesh = new THREE.InstancedMesh(geo, mat, N); scene.add(mesh);
    const GA = Math.PI * (1 + Math.sqrt(5));
    const stops = PALETTES[cfg.palette] || PALETTES.span;
    pts = [];
    if (cfg.shape === "ring") {
      // 2D-ring van kubussen (dcyoung 'diffusedRing'-idee, ARM64-licht):
      // diffuus randeffect via een vaste per-punt radiale spreiding
      for (let i = 0; i < N; i++) {
        const theta = i / N * Math.PI * 2;
        const seed = 0.6 + 0.4 * Math.abs(Math.sin(i * 12.9898) * 43758.5453 % 1);
        pts.push({ x: Math.cos(theta), y: Math.sin(theta), z: 0,
                   phi: theta % Math.PI, theta, seed });
        mesh.setColorAt(i, paletteColor(stops, i / N));
      }
    } else {
      // Fibonacci-bol
      for (let i = 0; i < N; i++) {
        const k = i + 0.5, phi = Math.acos(1 - 2*k/N), theta = (GA*k) % (Math.PI*2);
        pts.push({ x: Math.cos(theta)*Math.sin(phi), y: Math.sin(theta)*Math.sin(phi),
                   z: Math.cos(phi), phi, theta, seed: 1 });
        mesh.setColorAt(i, paletteColor(stops, i / N));
      }
    }
    mesh.instanceColor.needsUpdate = true;
    bars = new Float32Array(NB);
  }

  function updateBars() {
    const lvl = SPAN.micLevel || 0, st = SPAN.state;
    for (let b = 0; b < NB; b++) {
      let target;
      if (st === "speaking") target = lvl*(0.45+0.55*Math.sin(t*6+b*0.7))*(0.6+0.4*Math.sin(t*1.3+b));
      else if (st === "listening") target = lvl*0.85*(0.5+0.5*Math.sin(t*3+b*0.5));
      else if (st === "busy") target = 0.20*(0.5+0.5*Math.sin(t*5+b*1.1));
      else target = 0.05*(0.5+0.5*Math.sin(t*0.8+b*0.4));
      bars[b] += (Math.max(0, target) - bars[b]) * (cfg.smooth || 0.25);
    }
  }

  function frame() {
    if (!SPAN._orbActive) { raf = 0; return; }
    t += 0.016; updateBars();
    const st = SPAN.state;
    const rotT = (st==="busy"?0.9 : st==="speaking"?0.45 : st==="listening"?0.28 : 0.12) * cfg.rotation;
    rot += (rotT - rot) * 0.05;
    const pulse = 0.25 * cfg.pulse + flare * 0.4; flare *= 0.93;
    const N = pts.length;
    for (let i = 0; i < N; i++) {
      const p = pts[i];
      const band = Math.min(NB-1, (p.phi/Math.PI*NB) | 0);
      const disp = bars[band] * (0.7 + 0.3*Math.sin(p.theta*3 + t)) * (p.seed || 1);
      const r = cfg.radius * (1 + pulse * disp);
      m4.makeTranslation(p.x*r, p.y*r, p.z*r);
      mesh.setMatrixAt(i, m4);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.rotation.y += 0.002 + rot*0.004; mesh.rotation.x = 0.25;
    // tint: glitch=rood / reactorOk=groen, dooft uit (globaal via material.color)
    tintAmt *= 0.95;
    mat.color.setRGB(1,1,1).lerp(tint, tintAmt * 0.75);
    renderer.render(scene, cam);
    raf = requestAnimationFrame(frame);
  }

  function start() { if (!raf) { raf = requestAnimationFrame(frame); } }

  function activate(on) {
    SPAN._orbActive = !!on && hasTHREE;
    if (cv) cv.style.display = SPAN._orbActive ? "block" : "none";
    classic.style.visibility = SPAN._orbActive ? "hidden" : "visible";
    if (SPAN._orbActive) start();
  }

  SPAN.applyOrbConfig = (partial) => {
    const before = cfg;
    cfg = Object.assign({}, cfg, partial || {});
    try { localStorage.setItem("span_orb", JSON.stringify(cfg)); } catch (e) {}
    if (!hasTHREE) return;
    if (!renderer) buildCanvas();
    const rebuild = !mesh || partial && (partial.cubes !== undefined || partial.palette !== undefined
      || partial.cubeSize !== undefined || partial.radius !== undefined || partial.shape !== undefined);
    if (rebuild) buildMesh();
    activate(cfg.style === "orb");
  };
  SPAN.orbConfig = () => Object.assign({}, cfg);

  // tint-hooks aanhaken op de bestaande fx-functies
  const _glitch = SPAN.glitch, _ok = SPAN.reactorOk, _flare = SPAN.flare;
  SPAN.glitch = function(){ _glitch && _glitch.apply(this, arguments); if (tint){tint.set(0xff5a5a); tintAmt=1;} };
  SPAN.reactorOk = function(){ _ok && _ok.apply(this, arguments); if (tint){tint.set(0x7dffb4); tintAmt=1;} };
  SPAN.flare = function(){ _flare && _flare.apply(this, arguments); flare = 1; };

  // init
  if (hasTHREE && cfg.style === "orb") SPAN.applyOrbConfig({});
  else SPAN._orbActive = false;
})();
