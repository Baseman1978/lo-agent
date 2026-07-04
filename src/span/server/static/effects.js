/* SPAN DOM/tekst/hero-effecten — bouwt op SPAN + SPANFX, raakt geen logica.
   TextScramble (soulwire-recept), count-up, reticle, reveals, hero-overlays. */
"use strict";
(() => {
  const SPAN = window.SPAN, FX = window.SPANFX;
  const $ = (id) => document.getElementById(id);

  /* -- TextScramble (#22-23): per teken random start/duur, 28% flikker ------ */
  const SCRAMBLE_CHARS = "!<>-_\\/[]{}—=+*^?#________";
  class TextScramble {
    constructor(el) { this.el = el; this.update = this.update.bind(this); }
    setText(newText) {
      const old = this.el.textContent;
      const len = Math.max(old.length, newText.length);
      this.queue = [];
      for (let i = 0; i < len; i++) {
        const start = Math.floor(Math.random() * 24);
        this.queue.push({ from: old[i] || "", to: newText[i] || "",
          start, end: start + Math.floor(Math.random() * 24) });
      }
      cancelAnimationFrame(this.raf);
      this.frame = 0;
      this.update();
    }
    update() {
      let out = "", done = 0;
      for (const q of this.queue) {
        if (this.frame >= q.end) { done++; out += q.to; }
        else if (this.frame >= q.start) {
          if (!q.char || Math.random() < 0.28) {
            q.char = SCRAMBLE_CHARS[Math.floor(Math.random() * SCRAMBLE_CHARS.length)];
          }
          out += q.char;
        } else out += q.from;
      }
      this.el.textContent = out;
      this.frame++;
      if (done < this.queue.length) this.raf = requestAnimationFrame(this.update);
    }
  }
  SPAN.scramble = (el, text) => {
    if (!FX.on(1)) { el.textContent = text; return; }
    (el._scr = el._scr || new TextScramble(el)).setText(text);
  };

  /* paneel-titels decoderen bij boot (#22) */
  setTimeout(() => {
    if (!FX.on(1)) return;
    document.querySelectorAll(".panel h2").forEach((h, i) => {
      const node = h.childNodes[0];
      if (node && node.nodeType === 3) {
        const span = document.createElement("span");
        span.textContent = node.textContent;
        h.replaceChild(span, node);
        setTimeout(() => SPAN.scramble(span, span.textContent), i * 150);
      }
    });
  }, 2600);

  /* glyph-flicker op labels (#25) */
  setInterval(() => {
    if (!FX.on(2)) return;
    const titles = document.querySelectorAll(".panel h2 span");
    const t = titles[Math.floor(Math.random() * titles.length)];
    if (!t || t._scr && t._scr.raf) return;
    const orig = t.textContent;
    const i = Math.floor(Math.random() * orig.length);
    t.textContent = orig.slice(0, i) +
      SCRAMBLE_CHARS[Math.floor(Math.random() * SCRAMBLE_CHARS.length)] + orig.slice(i + 1);
    setTimeout(() => { t.textContent = orig; }, 120);
  }, 9000);

  /* -- count-up cijfers (#24): observeert het brein-paneel ------------------ */
  SPAN.countUp = (el, target) => {
    if (!FX.on(1) || !Number.isFinite(target)) { el.textContent = target; return; }
    const start = parseInt(el.textContent) || 0;
    if (start === target) { el.textContent = target; return; }
    const t0 = performance.now(), dur = 700;
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      el.textContent = Math.round(start + (target - start) * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes.forEach((n) => {
        if (n.nodeType === 1 && n.classList?.contains("bigstat")) {
          const b = n.querySelector("b");
          const v = parseInt(b?.textContent);
          if (b && Number.isFinite(v)) { b.textContent = "0"; SPAN.countUp(b, v); }
        }
      });
    }
  }).observe($("brein"), { childList: true });

  /* -- staggered panel reveal na boot (#34) --------------------------------- */
  document.querySelectorAll("aside .panel").forEach((p, i) => {
    p.style.opacity = "0"; p.style.transform = "scaleY(.6)";
    setTimeout(() => {
      p.style.transition = "opacity .4s ease, transform .4s ease";
      p.style.opacity = ""; p.style.transform = "";
    }, 2300 + i * 130);
  });

  /* -- focus-dimming (#38): actief paneel helder, rest dimt ------------------ */
  document.querySelectorAll(".panel").forEach((p) => {
    p.addEventListener("mouseenter", () => {
      if (!FX.on(2)) return;
      document.querySelectorAll(".panel").forEach((q) => q.classList.toggle("dimmed", q !== p));
    });
    p.addEventListener("mouseleave", () =>
      document.querySelectorAll(".panel").forEach((q) => q.classList.remove("dimmed")));
  });

  /* -- targeting reticle cursor (#77) ---------------------------------------- */
  if (FX.on(1) && matchMedia("(pointer: fine)").matches) {
    const ret = document.createElement("div");
    ret.id = "reticle";
    ret.innerHTML = '<div class="ret-ring"></div><div class="ret-dot"></div>';
    document.body.appendChild(ret);
    let locked = false;
    addEventListener("mousemove", (e) => {
      ret.style.left = e.clientX + "px"; ret.style.top = e.clientY + "px";
      const target = e.target.closest("button, a, select, .item, .inbox-card, .palette-item");
      if (!!target !== locked) { locked = !!target; ret.classList.toggle("lock", locked); }
    });
  }

  /* -- terminal-cursor tijdens streamen (#27) -------------------------------- */
  const style = document.createElement("style");
  style.textContent = ".msg.span:last-child:not(.done)::after{content:'▍';color:var(--cyan);animation:blinkdot 1s infinite}";
  document.head.appendChild(style);

  /* -- wake-acknowledge (#90): alle panelen pulsen synchroon ------------------ */
  SPAN.acknowledge = () => {
    if (!FX.on(1)) return;
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.remove("ack"); void p.offsetWidth; p.classList.add("ack");
    });
  };

  /* -- hero: dagstart-overlay (#87) ------------------------------------------- */
  SPAN.heroDaily = (dateLabel, spoken) => {
    if (!FX.on(1)) return;
    const ov = document.createElement("div");
    ov.className = "hero-overlay";
    ov.innerHTML = `<div class="hero-card"><div class="hero-date"></div>
      <div class="hero-body"></div><div class="hero-hint">klik om te sluiten</div></div>`;
    document.body.appendChild(ov);
    SPAN.scramble(ov.querySelector(".hero-date"), dateLabel);
    setTimeout(() => SPAN.scramble(ov.querySelector(".hero-body"),
      spoken.slice(0, 220) + (spoken.length > 220 ? "…" : "")), 400);
    const close = () => { ov.classList.add("gone"); setTimeout(() => ov.remove(), 600); };
    ov.onclick = close;
    setTimeout(close, 12000);
  };

  /* -- hero: alert-takeover (#88) — voor urgente inbox-items ------------------ */
  SPAN.heroAlert = (title, detail) => {
    if (!FX.on(1)) return;
    const ov = document.createElement("div");
    ov.className = "hero-overlay alert";
    ov.innerHTML = `<div class="hero-card"><div class="hero-date"></div>
      <div class="hero-body"></div><div class="hero-hint">klik om te sluiten</div></div>`;
    document.body.appendChild(ov);
    SPAN.scramble(ov.querySelector(".hero-date"), "⚠ " + title);
    setTimeout(() => SPAN.scramble(ov.querySelector(".hero-body"), detail), 300);
    SPAN.chime(988, .15);
    const close = () => { ov.classList.add("gone"); setTimeout(() => ov.remove(), 600); };
    ov.onclick = close;
    setTimeout(close, 8000);
  };

  /* -- hero: shutdown-sequence (#93) ------------------------------------------ */
  SPAN.shutdown = () => {
    if (!FX.on(1)) return;
    document.querySelectorAll("aside .panel").forEach((p, i) => {
      setTimeout(() => {
        p.style.transition = "opacity .35s ease, transform .35s ease";
        p.style.opacity = "0"; p.style.transform = "scaleY(.1)";
      }, i * 110);
    });
    const ov = document.createElement("div");
    ov.className = "hero-overlay";
    ov.innerHTML = '<div class="hero-card"><div class="hero-date"></div></div>';
    document.body.appendChild(ov);
    setTimeout(() => SPAN.scramble(ov.querySelector(".hero-date"), "SYSTEMEN IN RUST"), 700);
  };

  /* -- belangrijke woorden oplichten (#28) — datums/tijden/bedragen ----------- */
  SPAN.highlightFacts = (el) => {
    if (!FX.on(2)) return;
    el.innerHTML = el.innerHTML.replace(
      /(?<![\w>])(\d{1,2}:\d{2}|\d{1,2} (?:januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)|€\s?[\d.,]+|morgen|vandaag|vrijdag|maandag|dinsdag|woensdag|donderdag|zaterdag|zondag)(?![\w<])/gi,
      '<span class="fact">$1</span>');
  };
})();
