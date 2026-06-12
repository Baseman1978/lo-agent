/* SPAN · J.A.R.V.I.S. HUD — core: state, websocket-chat, panelen, boot.
   Visuals in fx.js, spraak in voice.js, brein-hologram in hologram.js. */
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input");

/* gedeelde namespace voor fx.js / voice.js / hologram.js */
const SPAN = window.SPAN = {
  state: "boot",        // boot | idle | listening | busy | speaking
  micLevel: 0,          // 0..1, gezet door voice.js, gelezen door fx.js
  speakOn: true,
  busy: false,
};
let ws = null, current = null;

function token() {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl !== null) {
    localStorage.setItem("span_token", fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  let t = localStorage.getItem("span_token");
  if (t === null) {
    t = prompt("Toegangstoken (leeg laten op localhost):") || "";
    localStorage.setItem("span_token", t);
  }
  return t;
}
SPAN.authHeaders = () => ({ Authorization: "Bearer " + token() });

SPAN.setState = (next) => {
  SPAN.state = next;
  const label = $("state-label");
  const names = { idle: "STANDBY", listening: "LUISTERT…", busy: "DENKT…",
    speaking: "SPREEKT", boot: "BOOT" };
  label.textContent = names[next] || next.toUpperCase();
  label.classList.toggle("hot", next !== "idle");
};

/* -- audio chime (gedeeld) ----------------------------------------------- */
let actx = null;
SPAN.chime = (freq, dur) => {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const o = actx.createOscillator(), g = actx.createGain();
    o.type = "sine"; o.frequency.value = freq;
    g.gain.setValueAtTime(.12, actx.currentTime);
    g.gain.exponentialRampToValueAtTime(.001, actx.currentTime + dur + .15);
    o.connect(g).connect(actx.destination);
    o.start(); o.stop(actx.currentTime + dur + .2);
  } catch (e) { /* stil */ }
};

/* -- boot sequence ------------------------------------------------------- */
const BOOT_LINES = [
  "SPAN KERNEL v1 — initialisatie",
  new Date().toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" }) +
    " · " + (localStorage.getItem("span_mf_count") ? localStorage.getItem("span_mf_count") + " herinneringen aan boord" : "brein wordt gewekt"),
  "neo4j brein: verbinding",
  "ORQ.AI gateway: online",
  "geheugenindex: HNSW geladen",
  "spraakinterface: gereed",
  "hologram-renderer: WebGL",
  "ambient watcher: actief",
  "integraties: O365 · Asana · Telegram",
  "ALLE SYSTEMEN ONLINE",
];
function boot() {
  const el = $("boot-log");
  let i = 0;
  const tick = () => {
    if (i > 0) el.children[i - 1].classList.add("ok");
    if (i >= BOOT_LINES.length) {
      setTimeout(() => {
        $("boot").classList.add("gone");
        SPAN.setState("idle");
        SPAN.chime(660, .08);
      }, 450);
      return;
    }
    const div = document.createElement("div");
    div.textContent = BOOT_LINES[i++];
    el.appendChild(div);
    SPAN.chime(380 + i * 40, .03);
    setTimeout(tick, 160);
  };
  tick();
}

/* -- klok ------------------------------------------------------------------ */
function clock() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString("nl-NL");
  $("date").textContent = now.toLocaleDateString("nl-NL",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
setInterval(clock, 1000); clock();

/* -- chat ------------------------------------------------------------------ */
function el(cls, who) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  if (who) {
    const w = document.createElement("span");
    w.className = "who"; w.textContent = who; div.appendChild(w);
  }
  log.appendChild(div); log.scrollTop = log.scrollHeight;
  return div;
}
function md(text) {
  let h = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  h = h.replace(/```([\s\S]*?)```/g, (_, c) => "<pre>" + c.replace(/^\w*\n/, "") + "</pre>");
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  return h;
}
SPAN.sys = (text, cls) => {
  el(cls || "sys").textContent = text;
  if (cls === "warn" && SPAN.glitch) SPAN.glitch();
};

let wsWanted = true, reconnectDelay = 1500, reconnectTimer = 0;
function connect() {
  clearTimeout(reconnectTimer);
  if (ws && ws.readyState <= 1) return;  // al verbonden of bezig: geen dubbele socket
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/chat`);
  ws.onopen = () => {
    reconnectDelay = 1500;
    ws.send(JSON.stringify({ type: "hello", token: token() }));
    // browser-locatie voor de weer-tool (stil falen bij weigering)
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition((pos) => {
        if (ws && ws.readyState === 1) {
          ws.send(JSON.stringify({ type: "location",
            lat: pos.coords.latitude, lon: pos.coords.longitude }));
        }
      }, () => {}, { maximumAge: 600000, timeout: 8000 });
    }
  };
  ws.onmessage = (event) => handle(JSON.parse(event.data));
  ws.onclose = () => {
    SPAN.busy = false; SPAN.setState("idle");
    if (!wsWanted) return;  // bewust gesloten (na evaluatie)
    SPAN.sys("Verbinding verbroken — opnieuw verbinden…");
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(15000, reconnectDelay * 1.6);
  };
}

/* dagstart: één keer per dag tonen + voorlezen (of geforceerd via settings) */
SPAN.playDaily = async (force) => {
  try {
    const res = await fetch("/api/jarvis/daily" + (force ? "?force=true" : ""),
      { headers: SPAN.authHeaders() });
    if (!res.ok) return;
    const d = await res.json();
    if (!force && localStorage.getItem("span_daily_shown") === d.date) return;
    localStorage.setItem("span_daily_shown", d.date);
    const div = el("span", "SPAN · DAGSTART");
    div.innerHTML = '<span class="who">SPAN · DAGSTART</span>' + md(d.spoken);
    SPAN.chime(660, .1);
    if (SPAN.heroDaily) {
      SPAN.heroDaily(new Date().toLocaleDateString("nl-NL",
        { weekday: "long", day: "numeric", month: "long" }), d.spoken);
    }
    if (SPAN.speakOn && SPAN.speak) SPAN.speak(d.spoken, true);  // dagstart volledig
  } catch (e) { /* stil */ }
};

function handle(msg) {
  if (msg.type === "ready") {
    SPAN.sys("Span is wakker — alle systemen online.");
    loadPanels();
    setTimeout(() => SPAN.playDaily(false), 2200);
  }
  else if (msg.type === "session") {
    SPAN.sys(`sessie ${msg.session_id} · ${msg.protocols} protocollen · ${msg.relevant} herinneringen`);
  }
  else if (msg.type === "delta") {
    if (!current) current = el("span", "SPAN");
    current.dataset.raw = (current.dataset.raw || "") + msg.text;
    current.innerHTML = '<span class="who">SPAN</span>' + md(current.dataset.raw);
    log.scrollTop = log.scrollHeight;
    if (SPAN.speakDelta) SPAN.speakDelta(msg.text);  // streaming TTS per zin
  }
  else if (msg.type === "touched") {
    if (SPAN.highlightNodes) SPAN.highlightNodes(msg.ids || []);
  }
  else if (msg.type === "done") {
    if (!current) {
      current = el("span", "SPAN");
      current.innerHTML = '<span class="who">SPAN</span>' + md(msg.answer);
    }
    if (current) {
      current.classList.add("done");
      if (SPAN.highlightFacts) SPAN.highlightFacts(current);
    }
    if (SPAN.reactorOk) SPAN.reactorOk();
    current = null; SPAN.busy = false; SPAN.setState("idle");
    if (SPAN.speakOn && SPAN.speakFlush) SPAN.speakFlush();  // rest van de stream
    if (turnStart) {
      const stat = document.getElementById("latency-stat");
      if (stat) stat.textContent = ((Date.now() - turnStart) / 1000).toFixed(1) + " s";
      turnStart = 0;
    }
    loadPanels();
    if (SPAN.refreshHologram) SPAN.refreshHologram();
  }
  else if (msg.type === "summary") {
    wsWanted = false;  // server sluit zo; niet her-verbinden maar vers herladen
    if (SPAN.shutdown) SPAN.shutdown();
    SPAN.sys("Sessie geëvalueerd: " + msg.summary);
    const written = Object.entries(msg.written || {}).map(([k, v]) => `${k}: ${v.length}`).join(", ");
    if (written) SPAN.sys("Vastgelegd — " + written);
    SPAN.sys("Nieuwe sessie start over enkele seconden…");
    setTimeout(() => location.reload(), 4500);
  }
  else if (msg.type === "error") {
    SPAN.sys(msg.message || "Fout", "warn");
    if (msg.error === "auth") localStorage.removeItem("span_token");
  }
}

let turnStart = 0;
SPAN.send = (textOverride) => {
  const text = (textOverride ?? input.value).trim();
  if (!text || SPAN.busy || !ws || ws.readyState !== 1) return;
  turnStart = Date.now();
  el("user", "JIJ").appendChild(document.createTextNode(text));
  ws.send(JSON.stringify({ type: "user", text }));
  input.value = ""; input.style.height = "auto";
  SPAN.busy = true; SPAN.setState("busy");
};

/* -- panelen ------------------------------------------------------------- */
function item(t, meta, cls) {
  const div = document.createElement("div");
  div.className = "item" + (cls ? " " + cls : "");
  const s = document.createElement("span"); s.className = "t"; s.textContent = t;
  div.appendChild(s);
  if (meta) {
    const m = document.createElement("span");
    m.className = "m"; m.innerHTML = meta; div.appendChild(m);
  }
  return div;
}
function fill(id, nodes, emptyText) {
  const box = $(id); box.innerHTML = "";
  if (!nodes.length) { box.innerHTML = `<div class="empty">${emptyText}</div>`; return; }
  nodes.forEach((n) => box.appendChild(n));
}
const esc = SPAN.esc = (s) => String(s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
const hhmm = (iso) => (iso || "").slice(11, 16);

/* panelen in herkenbare foutstaat zetten i.p.v. eeuwig 'geen data' */
function panelsError(reason) {
  for (const id of ["agenda", "taken", "mail", "quests"]) {
    const box = $(id);
    if (box && !box.querySelector(".item")) {
      box.innerHTML = `<div class="empty warn-text">kon niet verversen (${esc(reason)}) — probeert het zo opnieuw</div>`;
    }
  }
}

async function loadPanels() {
  try {
    const res = await fetch("/api/jarvis/briefing", { headers: SPAN.authHeaders() });
    if (!res.ok) { panelsError("HTTP " + res.status); return; }
    const d = await res.json();

    fill("agenda", (d.calendar || []).map((e) =>
      item(e.subject || "(zonder titel)",
        `${e.all_day ? "hele dag" : hhmm(e.start) + "–" + hhmm(e.end)}` +
        (e.location ? " · " + esc(e.location) : ""))),
      d.integrations && d.integrations.o365 ? "agenda leeg vandaag" : "O365 niet verbonden");

    const taken = [
      ...(d.asana || []).map((t) => item(t.name,
        (t.due ? `<span class="due">${t.due}</span> · ` : "") + "Asana" +
        (t.projects && t.projects.length ? " · " + esc(t.projects[0]) : ""))),
      ...(d.todo || []).map((t) => item(t.title,
        (t.due ? `<span class="due">${t.due}</span> · ` : "") + "To Do")),
    ];
    fill("taken", taken, "geen open taken");

    const mailItems = (d.mail || d.unread_mail || []).map((m) => {
      const it = item(m.subject || "(zonder onderwerp)",
        esc(m.from || "") + (m.unread ? ' · <span class="due">nieuw</span>' : ""));
      if (!m.unread) it.classList.add("read");
      return it;
    });
    fill("mail", mailItems,
      d.integrations && d.integrations.o365 ? "inbox leeg ✦" : "O365 niet verbonden");
    const unreadCount = (d.unread_mail || []).length;
    const mailTitle = document.querySelector("#panel-mail h2 span");
    if (mailTitle) mailTitle.textContent = "⟢ Mail" + (unreadCount ? ` · ${unreadCount} nieuw` : "");

    const REPEAT_LABEL = { once: "eenmalig", daily: "dagelijks",
      weekdays: "werkdagen", weekly: "wekelijks" };
    const questItems = [
      ...(d.quests || []).map((q) =>
        item(q.title, q.status, q.status === "active" ? "now" : "")),
      ...(d.crons || []).map((c) =>
        item("⏰ " + c.text,
          `${c.at} · ${REPEAT_LABEL[c.repeat] || c.repeat}` +
          (c.mode === "execute" ? " · voert zelf uit" : ""))),
    ];
    fill("quests", questItems, "geen open quests of geplande taken");

  } catch (e) { panelsError("netwerk"); }
  try {
    const res = await fetch("/api/status", { headers: SPAN.authHeaders() });
    if (!res.ok) return;
    const c = (await res.json()).counts;
    $("brein").innerHTML = "";
    if (Number.isFinite(c.MemoryFragment)) {
      localStorage.setItem("span_mf_count", c.MemoryFragment);
    }
    for (const [label, key] of [["herinneringen", "MemoryFragment"], ["inzichten", "Insight"],
      ["skills", "Skill"], ["quests", "Quest"], ["sessies", "Session"]]) {
      const div = document.createElement("div");
      div.className = "bigstat";
      div.innerHTML = `<span>${label}</span><b>${c[key]}</b>`;
      $("brein").appendChild(div);
    }
    const lat = document.createElement("div");
    lat.className = "bigstat";
    lat.innerHTML = `<span>laatste antwoord</span><b id="latency-stat">—</b>`;
    $("brein").appendChild(lat);
  } catch (e) { /* stil */ }
}
setInterval(loadPanels, 90000);

/* -- O365 device login ----------------------------------------------------- */
let o365Poll = 0;
function o365Status(text, isError) {
  // code + status zichtbaar in de settings-rij zelf (de chat zit achter de overlay)
  const slot = $("set-o365-code");
  if (slot) {
    slot.textContent = text;
    slot.classList.toggle("warn-text", !!isError);
  }
  SPAN.sys(text, isError ? "warn" : undefined);
}
$("o365-login").onclick = async () => {
  clearInterval(o365Poll);
  try {
    const res = await fetch("/api/auth/o365/start", { method: "POST", headers: SPAN.authHeaders() });
    const d = await res.json();
    if (!res.ok) { o365Status(d.detail || "Login starten mislukt", true); return; }
    o365Status(`Ga naar ${d.verification_uri} en voer code ${d.user_code} in.`);
    window.open(d.verification_uri, "_blank");
    let tries = 0;
    o365Poll = setInterval(async () => {
      try {
        if (++tries > 90) {  // ~6 min: device code is dan toch verlopen
          clearInterval(o365Poll);
          o365Status("Login verlopen — klik opnieuw op verbinden.", true);
          return;
        }
        const s = await (await fetch("/api/auth/o365/status", { headers: SPAN.authHeaders() })).json();
        if (s.authenticated) {
          clearInterval(o365Poll);
          o365Status(`Microsoft 365 verbonden: ${s.account}`);
          SPAN.chime(880, .12); loadPanels();
          if (SPAN.refreshSettings) SPAN.refreshSettings();
        } else if (s.flow && s.flow.status === "error") {
          clearInterval(o365Poll);
          o365Status("O365 login mislukt: " + s.flow.error, true);
        }
      } catch (e) { /* netwerk-hapering: volgende tick opnieuw */ }
    }, 4000);
  } catch (e) { o365Status("Login starten mislukt.", true); }
};

$("o365-logout").onclick = async () => {
  if (!confirm("Microsoft 365 ontkoppelen?")) return;
  try {
    const res = await fetch("/api/auth/o365/logout", { method: "POST", headers: SPAN.authHeaders() });
    const d = await res.json();
    if (!res.ok) { SPAN.sys(d.detail || "Ontkoppelen mislukt", "warn"); return; }
    SPAN.sys(`Microsoft 365 ontkoppeld (${d.account || "account"}). Koppel opnieuw via de login-knop.`);
    loadPanels();
    if (SPAN.refreshSettings) SPAN.refreshSettings();
  } catch (e) { SPAN.sys("Ontkoppelen mislukt.", "warn"); }
};

/* -- documenten naar het geheugen (📎 + drag & drop) ----------------------- */
async function uploadDoc(file) {
  SPAN.sys(`Document '${file.name}' verwerken…`);
  SPAN.setState("busy");
  try {
    const res = await fetch("/api/documents?filename=" + encodeURIComponent(file.name), {
      method: "POST",
      headers: { ...SPAN.authHeaders(), "Content-Type": "application/octet-stream" },
      body: await file.arrayBuffer(),
    });
    const d = await res.json();
    if (!res.ok) { SPAN.sys(`'${file.name}': ${d.detail || "mislukt"}`, "warn"); return; }
    SPAN.chime(880, .12);
    SPAN.sys(`'${d.title}' opgenomen in het geheugen — ${d.chunks} delen` +
      (d.truncated ? " (groot document: deels, samenvatting dekt het geheel)" : "") +
      (d.summary ? `. ${d.summary}` : ""));
    loadPanels();
    if (SPAN.refreshHologram) SPAN.refreshHologram();
  } catch (e) {
    SPAN.sys(`'${file.name}' uploaden mislukt.`, "warn");
  } finally {
    if (SPAN.state === "busy") SPAN.setState("idle");
  }
}
$("doc").onclick = () => $("doc-file").click();
$("doc-file").onchange = async () => {
  for (const f of $("doc-file").files) await uploadDoc(f);
  $("doc-file").value = "";
};
addEventListener("dragover", (e) => { e.preventDefault(); });
addEventListener("drop", async (e) => {
  e.preventDefault();
  for (const f of e.dataTransfer.files) await uploadDoc(f);
});

/* -- Escape sluit de bovenste open overlay ---------------------------------- */
addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const id of ["holo-overlay", "settings-overlay", "inbox-overlay"]) {
    const ov = $(id);
    if (ov && ov.classList.contains("open")) {
      if (id === "holo-overlay") {
        $("holo-close").click();  // verhuist de 3D-scene netjes terug
      } else {
        ov.classList.remove("open");
      }
      e.preventDefault();
      return;
    }
  }
});

/* -- invoer ----------------------------------------------------------------- */
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); SPAN.send(); }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 150) + "px";
});
$("end").onclick = () => {
  if (ws && ws.readyState === 1) {
    SPAN.sys("Sessie afsluiten — evaluatie draait…");
    SPAN.setState("busy");
    ws.send(JSON.stringify({ type: "end" }));
  } else {
    SPAN.sys("Geen actieve verbinding — opnieuw verbinden…", "warn");
    connect();
  }
};

/* -- start ----------------------------------------------------------------- */
boot();
connect();
