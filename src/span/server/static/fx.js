/* SPAN HUD visuals — uitbreiding van de bestaande laag, zelfde API:
   leest SPAN.state/SPAN.micLevel, levert SPAN.glitch, SPAN.ripple, SPAN.fxLevel. Ontwerpwetten (Territory/Hansen): ambient
   effecten zijn coherente achtergrond, hero's duren ±3s, glitch = fout.

   FX-fundament: intensiteit (uit/subtiel/vol) + FPS-budget dat ambient
   effecten getrapt uitschakelt + prefers-reduced-motion. */
"use strict";
(() => {
  const SPAN = window.SPAN;

  /* -- fundament: intensiteit + fps-budget --------------------------------- */
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const FX = window.SPANFX = {
    level: reduced ? 0 : parseInt(localStorage.getItem("span_fx") ?? "2"), // 0 uit, 1 subtiel, 2 vol
    fps: 60,
    budgetCut: 0, // 0 = alles, 1 = zware ambient uit, 2 = bijna alles uit
  };
  FX.on = (need) => FX.level >= need && FX.budgetCut < need;
  let frames = 0, fpsT = performance.now();
  function fpsTick() {
    frames++;
    const now = performance.now();
    if (now - fpsT >= 2000) {
      FX.fps = frames / ((now - fpsT) / 1000);
      frames = 0; fpsT = now;
      FX.budgetCut = FX.fps < 35 ? 2 : FX.fps < 50 ? 1 : 0; // #97 budget-manager
    }
  }

  /* -- achtergrond-canvas --------------------------------------------------- */
  const fx = document.getElementById("fx"), fxc = fx.getContext("2d");
  let parts = [], hexes = [], scans = [], rain = [], stars = [], ripples = [], keyEcho = [];
  let comet = null, nebula = [];
  let mouseX = .5, mouseY = .5;
  addEventListener("mousemove", (e) => {
    mouseX = e.clientX / innerWidth; mouseY = e.clientY / innerHeight;
  });

  function fxResize() {
    fx.width = innerWidth; fx.height = innerHeight;
    parts = Array.from({ length: Math.min(140, innerWidth / 10) }, () => ({
      x: Math.random() * fx.width, y: Math.random() * fx.height,
      r: Math.random() * 1.6 + .4, s: Math.random() * .35 + .08,
      a: Math.random() * Math.PI * 2, layer: Math.floor(Math.random() * 3), // #12 parallax
    }));
    hexes = Array.from({ length: Math.min(34, innerWidth / 42) }, () => ({
      x: Math.random() * fx.width, y: Math.random() * fx.height,
      r: Math.random() * 36 + 14, a: Math.random() * Math.PI,
      spd: (Math.random() - .5) * .004, drift: Math.random() * .12 + .02,
      op: Math.random() * .14 + .04, pulse: 0,
    }));
    scans = Array.from({ length: 5 }, () => newScan());
    rain = Array.from({ length: 14 }, () => newRainCol());   // #14 data-rain marges
    stars = Array.from({ length: 160 }, () => ({              // #17 sterrenkaart
      x: Math.random() * fx.width, y: Math.random() * fx.height,
      r: Math.random() * 1.2 + .3, tw: Math.random() * Math.PI * 2,
    }));
    nebula = Array.from({ length: 3 }, (_, i) => ({           // #15 nevel
      x: Math.random() * fx.width, y: Math.random() * fx.height,
      r: 260 + i * 120, vx: (Math.random() - .5) * .12, vy: (Math.random() - .5) * .08,
    }));
  }
  function newScan() {
    return { y: Math.random() * fx.height, len: Math.random() * 260 + 120,
      x: Math.random() * fx.width, vx: Math.random() * 1.4 + .6,
      op: Math.random() * .25 + .1 };
  }
  function newRainCol() {
    const margin = 90;
    const left = Math.random() < .5;
    return {
      x: left ? Math.random() * margin : fx.width - Math.random() * margin,
      y: Math.random() * fx.height, v: Math.random() * 1.6 + .8,
      chars: Array.from({ length: 6 }, () => String.fromCharCode(0x30A0 + Math.random() * 60)),
    };
  }
  addEventListener("resize", fxResize); fxResize();

  /* publieke triggers (gebruikt door jarvis.js/effects.js) */
  SPAN.ripple = (x, y, color) => {                            // #16/#78 grid-puls + klik-rimpel
    if (!FX.on(1)) return;
    ripples.push({ x, y, r: 0, max: 240, color: color || "56,225,255" });
  };
  addEventListener("pointerdown", (e) => SPAN.ripple(e.clientX, e.clientY));
  addEventListener("keydown", () => {                          // #84 toets-echo
    if (!FX.on(2) || keyEcho.length > 6) return;
    const bar = document.getElementById("bar");
    if (!bar) return;
    const r = bar.getBoundingClientRect();
    keyEcho.push({ x: r.left + Math.random() * r.width, y: r.top - 8 - Math.random() * 30, life: 1 });
  });

  const isEvening = () => new Date().getHours() >= 18 || new Date().getHours() < 6;

  function hexPath(c, x, y, r, a) {
    c.beginPath();
    for (let i = 0; i < 6; i++) {
      const ang = a + (Math.PI / 3) * i;
      i === 0 ? c.moveTo(x + r * Math.cos(ang), y + r * Math.sin(ang))
              : c.lineTo(x + r * Math.cos(ang), y + r * Math.sin(ang));
    }
    c.closePath();
  }

  function fxDraw(ts) {
    fpsTick();
    fxc.clearRect(0, 0, fx.width, fx.height);
    if (FX.level === 0) { requestAnimationFrame(fxDraw); return; }
    const glow = SPAN.state === "busy" ? .95 : SPAN.state === "speaking" ? .75
      : SPAN.state === "listening" ? .85 : .45;
    const boost = 1 + SPAN.micLevel * 1.6;
    const px = (mouseX - .5), py = (mouseY - .5);            // parallax-offset

    // nevel (zware laag — alleen op 'vol' en met budget)
    if (FX.on(2)) {
      fxc.globalCompositeOperation = "lighter";
      for (const n of nebula) {
        n.x += n.vx; n.y += n.vy;
        if (n.x < -n.r) n.x = fx.width + n.r; if (n.x > fx.width + n.r) n.x = -n.r;
        if (n.y < -n.r) n.y = fx.height + n.r; if (n.y > fx.height + n.r) n.y = -n.r;
        const g = fxc.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r);
        g.addColorStop(0, `rgba(20,60,90,${.05 * glow})`);
        g.addColorStop(1, "rgba(20,60,90,0)");
        fxc.fillStyle = g;
        fxc.fillRect(n.x - n.r, n.y - n.r, n.r * 2, n.r * 2);
      }
      fxc.globalCompositeOperation = "source-over";
    }

    // grid
    fxc.strokeStyle = "rgba(22,49,78,.25)"; fxc.lineWidth = 1;
    for (let x = 0; x < fx.width; x += 90) {
      fxc.beginPath(); fxc.moveTo(x, 0); fxc.lineTo(x, fx.height); fxc.stroke();
    }
    for (let y = 0; y < fx.height; y += 90) {
      fxc.beginPath(); fxc.moveTo(0, y); fxc.lineTo(fx.width, y); fxc.stroke();
    }

    // sterrenkaart 's avonds, hexagons overdag (#17)
    if (isEvening() && FX.on(1)) {
      for (const s of stars) {
        s.tw += .02;
        fxc.beginPath();
        fxc.fillStyle = `rgba(190,230,255,${(.25 + Math.sin(s.tw) * .15) * glow})`;
        fxc.arc(s.x + px * 12, s.y + py * 8, s.r, 0, Math.PI * 2); fxc.fill();
      }
    } else {
      for (const h of hexes) {
        h.a += h.spd; h.y += h.drift * .3;
        h.pulse *= .94;
        if (h.y - h.r > fx.height) h.y = -h.r;
        fxc.strokeStyle = `rgba(56,225,255,${(h.op + h.pulse) * glow})`;
        fxc.lineWidth = 1;
        hexPath(fxc, h.x + px * 18, h.y + py * 12, h.r, h.a); fxc.stroke();
      }
    }

    // particles (3 parallax-lagen) + constellatie-lijnen (#12, #13)
    const near = [];
    for (const p of parts) {
      p.x += Math.cos(p.a) * p.s * (1 + p.layer * .4);
      p.y += Math.sin(p.a) * p.s * (1 + p.layer * .4);
      if (p.x < 0) p.x = fx.width; if (p.x > fx.width) p.x = 0;
      if (p.y < 0) p.y = fx.height; if (p.y > fx.height) p.y = 0;
      const dx = px * (8 + p.layer * 14), dy = py * (6 + p.layer * 10);
      fxc.beginPath();
      fxc.fillStyle = `rgba(56,225,255,${(p.r / 2) * glow * .5 * boost})`;
      fxc.arc(p.x + dx, p.y + dy, p.r * (1 + SPAN.micLevel * .8), 0, Math.PI * 2);
      fxc.fill();
      if (p.layer === 2) near.push(p);
    }
    if (FX.on(2)) {
      for (let i = 0; i < near.length; i++) {
        for (let j = i + 1; j < near.length; j++) {
          const dx = near[i].x - near[j].x, dy = near[i].y - near[j].y;
          const d2 = dx * dx + dy * dy;
          if (d2 < 6400) {
            fxc.strokeStyle = `rgba(56,225,255,${(1 - d2 / 6400) * .14 * glow})`;
            fxc.lineWidth = .6;
            fxc.beginPath(); fxc.moveTo(near[i].x, near[i].y);
            fxc.lineTo(near[j].x, near[j].y); fxc.stroke();
          }
        }
      }
    }

    // scanning lines
    for (const s of scans) {
      s.x += s.vx;
      if (s.x - s.len > fx.width) Object.assign(s, newScan(), { x: -s.len });
      const grad = fxc.createLinearGradient(s.x - s.len, 0, s.x, 0);
      grad.addColorStop(0, "rgba(56,225,255,0)");
      grad.addColorStop(1, `rgba(56,225,255,${s.op * glow})`);
      fxc.strokeStyle = grad; fxc.lineWidth = 1.2;
      fxc.beginPath(); fxc.moveTo(s.x - s.len, s.y); fxc.lineTo(s.x, s.y); fxc.stroke();
    }

    // data-rain in de marges (#14)
    if (FX.on(2)) {
      fxc.font = "10px Consolas, monospace";
      for (const col of rain) {
        col.y += col.v;
        if (col.y > fx.height + 80) Object.assign(col, newRainCol(), { y: -80 });
        col.chars.forEach((ch, i) => {
          fxc.fillStyle = `rgba(56,225,255,${(.28 - i * .04) * glow})`;
          fxc.fillText(ch, col.x, col.y - i * 12);
        });
        if (Math.random() < .05) col.chars[Math.floor(Math.random() * 6)] =
          String.fromCharCode(0x30A0 + Math.random() * 60);
      }
    }

    // vluchtpad-komeet, max ~1 per 30s (#19)
    if (!comet && FX.on(1) && Math.random() < .0006) {
      comet = { x: -60, y: Math.random() * fx.height * .5, vx: 7 + Math.random() * 4, vy: 1.6 };
    }
    if (comet) {
      comet.x += comet.vx; comet.y += comet.vy;
      const g = fxc.createLinearGradient(comet.x - 90, comet.y - 20, comet.x, comet.y);
      g.addColorStop(0, "rgba(56,225,255,0)"); g.addColorStop(1, "rgba(190,240,255,.8)");
      fxc.strokeStyle = g; fxc.lineWidth = 2;
      fxc.beginPath(); fxc.moveTo(comet.x - 90, comet.y - 20); fxc.lineTo(comet.x, comet.y); fxc.stroke();
      if (comet.x > fx.width + 100) comet = null;
    }

    // grid-puls / klik-rimpels (#16, #78)
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i];
      r.r += 6;
      const op = 1 - r.r / r.max;
      if (op <= 0) { ripples.splice(i, 1); continue; }
      fxc.strokeStyle = `rgba(${r.color},${op * .5})`; fxc.lineWidth = 1.5;
      fxc.beginPath(); fxc.arc(r.x, r.y, r.r, 0, Math.PI * 2); fxc.stroke();
      for (const h of hexes) {
        const d = Math.hypot(h.x - r.x, h.y - r.y);
        if (Math.abs(d - r.r) < 30) h.pulse = Math.max(h.pulse, .3 * op);
      }
    }

    // toets-echo (#84)
    for (let i = keyEcho.length - 1; i >= 0; i--) {
      const k = keyEcho[i];
      k.life -= .04; k.y -= .6;
      if (k.life <= 0) { keyEcho.splice(i, 1); continue; }
      fxc.strokeStyle = `rgba(56,225,255,${k.life * .5})`;
      hexPath(fxc, k.x, k.y, 7, 0); fxc.stroke();
    }

    requestAnimationFrame(fxDraw);
  }
  requestAnimationFrame(fxDraw);

  /* -- foutkleurtaal (was: arc-reactor; de NEBULA-scene is nu de visual) ---- */
  SPAN.glitch = () => {
    document.body.classList.add("glitching");
    setTimeout(() => document.body.classList.remove("glitching"), 700);
  };
})();
