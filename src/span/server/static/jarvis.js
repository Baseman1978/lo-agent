/* SPAN · J.A.R.V.I.S. HUD — core: state, websocket-chat, panelen, boot.
   Visuals in fx.js + de NEBULA-scene (hud/nebula.js), spraak in voice.js. */
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input");

/* gedeelde namespace voor fx.js / voice.js / settings.js */
const SPAN = window.SPAN = {
  state: "boot",        // boot | idle | listening | busy | speaking
  micLevel: 0,          // 0..1, gezet door voice.js, gelezen door fx.js
  speakOn: true,
  busy: false,
};
let ws = null, current = null;

function token() {
  // Auth loopt via de Microsoft-sessiecookie (SSO): nooit om een token vragen.
  // Een ?token= in de URL blijft ondersteund voor token-modus/dev; anders leeg
  // -> de httpOnly cookie regelt WS én REST. Verlopen sessie -> de foutafhandeling
  // stuurt naar /auth/login (geen prompt meer; dat schoot bij Ctrl+R te vroeg af
  // omdat SPAN.sso nog niet gezet was).
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl !== null) {
    localStorage.setItem("span_token", fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  return localStorage.getItem("span_token") || "";
}
SPAN.authHeaders = () => ({ Authorization: "Bearer " + token() });

SPAN.setState = (next) => {
  SPAN.state = next;
  // N3: de NEBULA-orb volgt de echte agent-status (busy heet daar thinking)
  if (SPAN._nebulaHandle && SPAN._nebulaHandle.setState) {
    SPAN._nebulaHandle.setState(next === "busy" ? "thinking" : next);
  }
};

/* -- "bezig"-indicator in de chat (tijdens denken/tool-aanroepen) -------- */
const TOOL_LABELS = {
  o365_mail_search: "🔎 Mail zoeken (alle mappen)", o365_archive_folder: "📥 Mailmap archiveren",
  o365_attachment_read: "📎 Bijlage lezen", o365_mail_attachments: "📎 Bijlagen ophalen",
  o365_mail_inbox: "📧 Inbox lezen", o365_mail_folders: "📂 Mappen ophalen",
  o365_calendar: "📅 Agenda lezen", o365_calendar_search: "📅 Agenda doorzoeken",
  o365_files_search: "📁 Bestanden zoeken", o365_file_read: "📄 Bestand lezen",
  o365_sharepoint_search: "🗂️ SharePoint doorzoeken", o365_teams_search: "💬 Teams doorzoeken",
  o365_people_search: "👤 Personen zoeken", o365_thread_summary: "📧 Mailthread samenvatten",
  o365_mail_send: "✉️ Mail klaarzetten", o365_event_create: "📅 Afspraak klaarzetten",
  brain_search: "🧠 Geheugen doorzoeken", brain_cypher: "🧠 Brein bevragen",
  remember: "🧠 Onthouden", web_search: "🌐 Web zoeken", web_read: "🌐 Webpagina lezen",
  asana_search: "✅ Asana doorzoeken", jarvis_briefing: "🗞️ Briefing maken",
};
SPAN.toolLabel = (n) => TOOL_LABELS[n] || ("⚙ " + String(n).replace(/^o365_/, "").replace(/_/g, " "));
SPAN.working = (text) => {
  const log = $("log"); if (!log) return;
  let el = document.getElementById("working");
  if (text === null) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div"); el.id = "working"; el.className = "working";
    log.appendChild(el);
  }
  el.innerHTML = `<span class="dots"><i></i><i></i><i></i></span> ${text}`;
  log.scrollTop = log.scrollHeight;
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
  "LO KERNEL v1 — initialisatie",
  new Date().toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" }) +
    " · " + (localStorage.getItem("span_mf_count") ? localStorage.getItem("span_mf_count") + " herinneringen aan boord" : "brein wordt gewekt"),
  "neo4j brein: verbinding",
  "ORQ.AI gateway: online",
  "geheugenindex: HNSW geladen",
  "spraakinterface: gereed",
  "NEBULA-renderer: WebGL2",
  "ambient watcher: actief",
  "integraties: O365 · Asana · Telegram",
  "ALLE SYSTEMEN ONLINE",
];
function boot() {
  const el = $("boot-log");
  BOOT_LINES[0] = (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO")
    .toUpperCase() + " KERNEL v1 — initialisatie";
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
// chatlabel van de agent: volgt AGENT_NAME (via SPAN._agentName), geëscaped
// omdat het who-label in innerHTML terechtkomt
function agentWho(suffix) {
  return (SPAN._agentName || "LO") + (suffix || "");
}
function whoTag(suffix) {
  const t = agentWho(suffix).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return '<span class="who">' + t + "</span>";
}
SPAN.sys = (text, cls) => {
  el(cls || "sys").textContent = text;
  if (cls === "warn" && SPAN.glitch) SPAN.glitch();
};

// online/offline rechtsboven via het bolletje i.p.v. meldingen in de chat.
// groen = WS verbonden ÉN brein ok; rood = WS weg óf brein down.
SPAN._wsOk = false; SPAN._health = null;
SPAN._applyDot = () => {
  const d = $("health-dot"); if (!d) return;
  const h = SPAN._health;
  const ok = SPAN._wsOk && (!h || h.brain);
  d.classList.toggle("ok", ok);
  d.classList.toggle("down", !ok);
  d.title = !SPAN._wsOk ? "offline — opnieuw verbinden…"
    : (h ? `online · brein: ${h.brain ? "ok" : "OFFLINE"} · o365: ${h.o365 ? "gekoppeld" : "—"}`
         + ` · asana: ${h.asana ? "gekoppeld" : "—"}`
        : "online");
};
SPAN.setOnline = (ok) => { SPAN._wsOk = ok; SPAN._applyDot(); };

// merknaam uit /auth/status (één bron in de backend: AGENT_NAME) -> HUD
SPAN._agentName = "LO";
SPAN.applyBranding = (name, tagline) => {
  if (name) {
    SPAN._agentName = name;
    const h1 = document.querySelector("header h1"); if (h1) h1.textContent = name;
    document.title = name + " · Lomans";
  }
  if (tagline) {
    const sub = document.querySelector("header .sub"); if (sub) sub.textContent = tagline;
  }
};
let wsWanted = true, reconnectDelay = 1500, reconnectTimer = 0;
function connect() {
  clearTimeout(reconnectTimer);
  if (ws && ws.readyState <= 1) return;  // al verbonden of bezig: geen dubbele socket
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/chat`);
  ws.onopen = () => {
    reconnectDelay = 1500;
    SPAN.setOnline(true);
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
  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); }
    catch (e) { SPAN.sys("Onleesbaar bericht van de server genegeerd.", "warn"); return; }
    try { handle(msg); }
    catch (e) { SPAN.busy = false; SPAN.setState("idle"); SPAN.sys("Fout bij verwerken bericht.", "warn"); }
  };
  ws.onclose = () => {
    SPAN.busy = false; SPAN.setState("idle");
    if (!wsWanted) return;  // bewust gesloten (na evaluatie)
    SPAN.setOnline(false);  // bolletje rood i.p.v. melding in de chat
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(15000, reconnectDelay * 1.6);
  };
}

/* C6: eenmalige welkomstuitleg voor nieuwe gebruikers. Sluiten (knop of
   klik ernaast) zet de localStorage-vlag zodat hij nooit meer terugkomt. */
SPAN.showOnboarding = () => {
  const ov = $("onboard-overlay"); if (!ov) return;
  const t = $("onboard-title");
  if (t) t.textContent = "Welkom — ik ben " + (SPAN._agentName || "LO");
  const done = () => {
    ov.classList.remove("open");
    localStorage.setItem("lo_onboarded", "1");
  };
  const b = $("onboard-start"); if (b) b.onclick = done;
  ov.addEventListener("click", (e) => { if (e.target === ov) done(); });
  ov.classList.add("open");
  if (b) b.focus();
};

/* dagstart: één keer per dag tonen + voorlezen (of geforceerd via settings) */
SPAN.playDaily = async (force) => {
  try {
    const res = await fetch("/api/jarvis/daily" + (force ? "?force=true" : ""),
      { headers: SPAN.authHeaders() });
    if (!res.ok) return;
    const d = await res.json();
    if (!force && localStorage.getItem("span_daily_shown") === d.date) return;
    localStorage.setItem("span_daily_shown", d.date);
    const div = el("span", agentWho(" · DAGSTART"));
    div.innerHTML = whoTag(" · DAGSTART") + md(d.spoken);
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
    SPAN.setOnline(true);
    if (!SPAN._welcomed) {  // welkomstmelding maar één keer, niet bij elke reconnect
      SPAN.sys(SPAN._agentName + " is wakker — alle systemen online.");
      SPAN._welcomed = true;
    }
    loadPanels();
    if (SPAN.showSuggestions) SPAN.showSuggestions();
    if (!localStorage.getItem("lo_onboarded")) SPAN.showOnboarding();
    setTimeout(() => SPAN.playDaily(false), 2200);
  }
  else if (msg.type === "session") {
    SPAN.sys(`sessie ${msg.session_id} · ${msg.protocols} protocollen · ${msg.relevant} herinneringen`);
  }
  else if (msg.type === "delta") {
    SPAN.working(null);  // er komt tekst -> indicator weg
    if (!current) current = el("span", agentWho());
    current.dataset.raw = (current.dataset.raw || "") + msg.text;
    current.innerHTML = whoTag() + md(current.dataset.raw);
    log.scrollTop = log.scrollHeight;
    if (SPAN.speakDelta) SPAN.speakDelta(msg.text);  // streaming TTS per zin
  }
  else if (msg.type === "tool") {
    // live tonen welke tool draait -> duidelijk dat Span bezig is
    if (msg.phase === "start") SPAN.working(SPAN.toolLabel(msg.name) + "…");
    else SPAN.working((SPAN._agentName || "LO") + " werkt verder…");
  }
  else if (msg.type === "memory_read") {
    // live: Span raadpleegt geheugen tijdens de beurt -> leescascade in de scene
    if (SPAN._nebulaHandle && SPAN._nebulaHandle.markReading) {
      SPAN._nebulaHandle.markReading(msg.ids || [], msg.reason || "");
    }
  }
  else if (msg.type === "done") {
    if (!current) {
      current = el("span", agentWho());
      current.innerHTML = whoTag() + md(msg.answer);
    }
    if (current) {
      current.classList.add("done");
      if (SPAN.highlightFacts) SPAN.highlightFacts(current);
    }
    SPAN.working(null);
    current = null; SPAN.busy = false; SPAN.setState("idle");
    { const st = $("stop"); if (st) st.classList.add("hidden"); }
    // bij een onderbroken beurt geen restant voorlezen; anders de stream afmaken
    if (!msg.cancelled && SPAN.speakOn && SPAN.speakFlush) SPAN.speakFlush();
    SPAN._muteStream = false;                               // klaar -> volgende antwoord mag weer
    if (SPAN._pendingText) {                                // barge-in: opgevangen vervolg nu sturen
      const t = SPAN._pendingText; SPAN._pendingText = null;
      setTimeout(() => SPAN.send(t), 60);
    }
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
    SPAN.working(null);
    SPAN.busy = false; SPAN.setState("idle");
    { const st = $("stop"); if (st) st.classList.add("hidden"); }
    if (msg.error === "auth") {
      if (SPAN.sso) { location.href = "/auth/login"; return; }  // sessie weg -> opnieuw inloggen
      localStorage.removeItem("span_token");
    }
  }
}

let turnStart = 0;
SPAN.send = (textOverride) => {
  const text = (textOverride ?? input.value).trim();
  if (!text || SPAN.busy || !ws || ws.readyState !== 1) return;
  SPAN._muteStream = false;   // een nieuw antwoord mag weer voorgelezen worden
  turnStart = Date.now();
  const sg = $("suggested"); if (sg) sg.innerHTML = "";  // suggesties weg zodra je begint
  el("user", "JIJ").appendChild(document.createTextNode(text));
  ws.send(JSON.stringify({ type: "user", text }));
  input.value = ""; input.style.height = "auto";
  SPAN.busy = true; SPAN.setState("busy");
  SPAN.working((SPAN._agentName || "LO") + " werkt…");  // meteen zichtbaar dat hij bezig is
  const st = $("stop"); if (st) st.classList.remove("hidden");
};

/* server vragen de lopende beurt af te breken (barge-in / stop-knop) */
SPAN.cancel = () => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "cancel" }));
};

/* stop: onderbreekt het voorlezen, breekt de serverbeurt echt af en geeft de
   UI weer vrij */
$("stop").onclick = () => {
  try { window.speechSynthesis && speechSynthesis.cancel(); } catch (e) { /* */ }
  if (SPAN.busy) SPAN.cancel();           // server: beurt daadwerkelijk stoppen
  SPAN._muteStream = true;                // resterende delta's niet meer voorlezen
  SPAN.busy = false; SPAN.setState("idle");
  $("stop").classList.add("hidden");
};

/* suggested prompts bij een lege chat — laagdrempelige startpunten */
const SUGGESTIONS = ["Geef me mijn briefing", "Wat staat er vandaag?",
  "Wat is er blijven liggen?", "Vat mijn laatste meeting samen"];
SPAN.showSuggestions = () => {
  const sg = $("suggested");
  if (!sg || log.querySelector(".msg.user")) return;  // alleen bij lege chat
  sg.innerHTML = "";
  SUGGESTIONS.forEach((s) => {
    const b = document.createElement("button");
    b.className = "suggestion"; b.textContent = s;
    b.onclick = () => SPAN.send(s);
    sg.appendChild(b);
  });
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
        (t.due ? `<span class="due">${esc(t.due)}</span> · ` : "") + "Asana" +
        (t.projects && t.projects.length ? " · " + esc(t.projects[0]) : ""))),
      ...(d.todo || []).map((t) => item(t.title,
        (t.due ? `<span class="due">${esc(t.due)}</span> · ` : "") + "To Do")),
    ];
    fill("taken", taken, "geen open taken");

    const mailItems = (d.mail || d.unread_mail || []).map((m) => {
      const it = item(m.subject || "(zonder onderwerp)",
        esc(m.from || "") + (m.unread ? ' · <span class="due">nieuw</span>' : ""));
      if (!m.unread) it.classList.add("read");
      return it;
    });
    // bron tijdelijk beperkt? toon dat i.p.v. een misleidend 'inbox leeg'
    const limited = d.mcp_status && d.mcp_status.kind !== "auth";
    const mailEmpty = limited ? esc(d.mcp_status.message)
      : (d.integrations && d.integrations.o365 ? "inbox leeg ✦" : "O365 niet verbonden");
    fill("mail", mailItems, mailEmpty);
    if (d.mcp_status) {
      SPAN.sys(d.mcp_status.message, d.mcp_status.kind === "auth" ? "warn" : undefined);
      if (d.mcp_status.kind === "rate_limited") backoffPanels();
    } else if (SPAN._panelsOk) {
      SPAN._panelsOk();  // schone ophaal -> ververssnelheid terug naar normaal
    }
    const unreadCount = (d.unread_mail || []).length;
    const mailTitle = document.querySelector("#panel-mail h2 span");
    if (mailTitle) mailTitle.textContent = "⟢ Mail" + (unreadCount ? ` · ${unreadCount} nieuw` : "");

    const REPEAT_LABEL = { once: "eenmalig", daily: "dagelijks",
      weekdays: "werkdagen", weekly: "wekelijks" };
    const questItems = [
      ...(d.quests || []).map((q) =>
        item(q.title, esc(q.status), q.status === "active" ? "now" : "")),
      ...(d.crons || []).map((c) =>
        item("⏰ " + c.text,
          `${esc(c.at)} · ${esc(REPEAT_LABEL[c.repeat] || c.repeat)}` +
          (c.mode === "execute" ? " · voert zelf uit" : ""))),
    ];
    fill("quests", questItems, "geen open quests of geplande taken");

    // meetings (Fireflies) — eigen endpoint; paneel alleen tonen mét data
    fetch("/api/meetings", { headers: SPAN.authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((md) => {
        if (!md) return;
        SPAN._meetingsOk = (md.meetings || []).length > 0;  // data telt, niet de koppelvorm (MCP of native)
        const plek = SPAN.panelLayout().meetings;
        const sec = document.querySelector('[data-panel="meetings"]');
        const tonen = SPAN._meetingsOk && plek !== "uit";
        if (sec) sec.classList.toggle("hidden", !tonen);
        if (tonen) {
          fill("meetings", md.meetings.map((m) =>
            item(m.title || "(zonder titel)",
                 `${esc(m.date || "")}${m.duration_min ? " · " + Math.round(m.duration_min) + " min" : ""}`)),
            "geen meetings");
        }
        SPAN.fitPanels();
      }).catch(() => { /* stil */ });

    SPAN.fitPanels();
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
    SPAN.fitPanels();
  } catch (e) { /* stil */ }
}
/* panelen verversen met back-off: normaal elke 90s, maar als de MCP-server
   rate-limit teruggeeft wachten we langer (tot 5 min) zodat we 'm niet verder
   overbelasten; bij een schone ophaal zakt het interval terug naar normaal. */
let panelEvery = 90000, panelTimer = 0;
function schedulePanels() {
  clearTimeout(panelTimer);
  panelTimer = setTimeout(() => { loadPanels(); schedulePanels(); }, panelEvery);
}
function backoffPanels() { panelEvery = Math.min(300000, panelEvery * 2); }
SPAN._panelsOk = () => { panelEvery = 90000; };  // door loadPanels bij succes
schedulePanels();

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
const OVERLAY_IDS = ["onboard-overlay", "perm-overlay",
                     "settings-overlay", "inbox-overlay", "tasks-overlay"];
addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (closeExpandedPanel()) { e.preventDefault(); return; }  // bento "+ n"-overlay
  for (const id of OVERLAY_IDS) {
    const ov = $(id);
    if (ov && ov.classList.contains("open")) {
      if (id === "onboard-overlay") {
        $("onboard-start").click();  // via de knop, zodat de gezien-vlag gezet wordt
      } else {
        ov.classList.remove("open");
      }
      e.preventDefault();
      return;
    }
  }
});

/* -- C7: focus-trap — Tab blijft binnen de open dialog; focus gaat bij openen
   naar binnen en keert bij sluiten terug naar de knop die hem opende. De
   open/close-paden zitten verspreid (settings.js, ambient.js), dus we kijken
   naar de class-wijziging zelf i.p.v. elk pad aan te passen. */
(function focusTrap() {
  const SEL = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), '
            + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const visible = (n) => n.offsetParent !== null;
  const topOpen = () => {
    for (const id of OVERLAY_IDS) {
      const ov = $(id);
      if (ov && ov.classList.contains("open")) return ov;
    }
    return null;
  };
  let restore = null;
  OVERLAY_IDS.forEach((id) => {
    const ov = $(id); if (!ov) return;
    let wasOpen = false;
    new MutationObserver(() => {
      const open = ov.classList.contains("open");
      if (open && !wasOpen) {
        restore = document.activeElement;
        if (!ov.contains(document.activeElement)) {
          const f = Array.from(ov.querySelectorAll(SEL)).filter(visible);
          if (f.length) f[0].focus();
        }
      } else if (!open && wasOpen && !topOpen()) {
        if (restore && document.body.contains(restore)) restore.focus();
        restore = null;
      }
      wasOpen = open;
    }).observe(ov, { attributes: true, attributeFilter: ["class"] });
  });
  addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const ov = topOpen(); if (!ov) return;
    const f = Array.from(ov.querySelectorAll(SEL)).filter(visible);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (!ov.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, true);
})();

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

/* -- weergave-modi: minimaal (stem) · chat · volledig ----------------------- */
const MODES = ["min", "chat", "full"];
SPAN.setMode = (m) => {
  if (!MODES.includes(m)) m = "full";
  document.body.classList.remove("mode-min", "mode-chat", "mode-full");
  document.body.classList.add("mode-" + m);
  localStorage.setItem("span_mode", m);
  document.querySelectorAll("#mode-switch button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === m));
  if (m === "full" && SPAN.fitPanels) SPAN.fitPanels();  // bento hermeten
};
SPAN.setMode(localStorage.getItem("span_mode") || "full");
document.querySelectorAll("#mode-switch button").forEach((b) =>
  b.addEventListener("click", () => SPAN.setMode(b.dataset.mode)));

/* -- panelen indelen (volledig-weergave): links / rechts / uit --------------- */
const PANEL_ORDER = ["agenda", "taken", "mail", "quests", "meetings", "brein"];
const PANEL_DEFAULT = { agenda: "links", taken: "links", mail: "rechts",
                        quests: "rechts", meetings: "rechts", brein: "uit" };
SPAN.panelLayout = () => {
  try { return { ...PANEL_DEFAULT, ...(JSON.parse(localStorage.getItem("span_panels") || "{}")) }; }
  catch (e) { return { ...PANEL_DEFAULT }; }
};
SPAN.applyPanelLayout = (cfg) => {
  const left = $("left"), right = $("right");
  for (const name of PANEL_ORDER) {
    const sec = document.querySelector(`[data-panel="${name}"]`);
    if (!sec) continue;
    const plek = cfg[name] || "uit";
    // meetings blijft óók verborgen zolang er geen data is (loader beslist)
    const verborgen = plek === "uit" || (name === "meetings" && !SPAN._meetingsOk);
    sec.classList.toggle("hidden", verborgen);
    if (plek === "links" && left) left.appendChild(sec);
    else if (plek === "rechts" && right) right.appendChild(sec);
  }
  SPAN.fitPanels();
};

/* -- bento: alles in één beeld, nooit scrollen ------------------------------
   Elke tegel krijgt hoogte naar inhoud (leeg = alleen kopregel) en toont
   zoveel items als er echt passen; de rest zit achter één "+ n"-regel die
   het paneel als overlay openklapt. Op mobiel (<=900px) blijft de native
   scroll — daar is vegen natuurlijker dan een overlay. */
SPAN.fitPanels = () => {
  const panels = [...document.querySelectorAll("main .panel")]
    .filter((p) => !p.classList.contains("hidden") && !p.classList.contains("expanded"));
  const smal = innerWidth <= 900;
  // pas 1: reset + hoogteverdeling naar inhoud (mobiel: alles native laten)
  for (const sec of panels) {
    const body = sec.querySelector(".body");
    if (!body) continue;
    sec.querySelector(".more-row")?.remove();
    body.querySelectorAll(".fit-hide").forEach((el) => el.classList.remove("fit-hide"));
    const n = body.querySelectorAll(".item, .bigstat").length;
    sec.classList.toggle("lean", !smal && n === 0);
    sec.style.flex = smal ? "" : (n === 0 ? "" : `${Math.min(n, 8)} 1 0%`);
  }
  if (smal) return;
  // pas 2: meten wat er past; rest verbergen achter "+ n". Synchroon — het
  // lezen van clientHeight forceert de layout na pas 1 (géén rAF: die vuurt
  // niet in achtergrond-tabs, waardoor de fit dan stil zou blijven staan).
  for (const sec of panels) {
    const body = sec.querySelector(".body");
    if (!body) continue;
    const items = [...body.querySelectorAll(".item, .bigstat")];
    if (!items.length) continue;
    const H = body.clientHeight;
    if (!H) continue;  // paneel niet zichtbaar (mode-min/chat): niets verbergen
    const hoogtes = items.map((el) => el.offsetHeight + 1);
    let past = 0, gebruikt = 0;
    for (const h of hoogtes) {
      if (gebruikt + h > H) break;
      gebruikt += h; past++;
    }
    if (past >= items.length) continue;
    // ruimte vrijhouden voor de "+ n"-regel zelf (die krimpt de body ~22px)
    while (past > 1 && gebruikt + 22 > H) { past--; gebruikt -= hoogtes[past]; }
    past = Math.max(past, 1);  // nooit een leeg paneel met alleen "▾ nog n"
    items.forEach((el, i) => el.classList.toggle("fit-hide", i >= past));
    const more = document.createElement("div");
    more.className = "more-row";
    more.textContent = `▾ nog ${items.length - past}`;
    more.onclick = () => expandPanel(sec);
    sec.appendChild(more);
  }
};
function expandPanel(sec) {
  closeExpandedPanel();
  const bd = document.createElement("div");
  bd.id = "panel-backdrop";
  bd.onclick = closeExpandedPanel;
  document.body.appendChild(bd);
  sec.classList.add("expanded");
  sec.querySelector(".more-row")?.remove();
  sec.querySelectorAll(".fit-hide").forEach((el) => el.classList.remove("fit-hide"));
}
function closeExpandedPanel() {
  const open = document.querySelector(".panel.expanded");
  $("panel-backdrop")?.remove();
  if (!open) return false;
  open.classList.remove("expanded");
  SPAN.fitPanels();
  return true;
}
let fitResizeTimer = null;
addEventListener("resize", () => {
  clearTimeout(fitResizeTimer);
  fitResizeTimer = setTimeout(() => SPAN.fitPanels(), 150);
});
SPAN.applyPanelLayout(SPAN.panelLayout());

/* -- NEBULA-scene: dé center-visual (N5: de klassieke orb is verwijderd) ----
   Geen WebGL2 -> geen 3D-scene; chat en panelen werken gewoon door. */
(function nebulaBoot() {
  let gl2 = false;
  try { gl2 = !!document.createElement("canvas").getContext("webgl2"); } catch (e) { /* stil */ }
  if (!gl2) { console.warn("[nebula] geen WebGL2 - geen 3D-scene"); return; }
  SPAN._nebula = true;
  document.body.classList.add("nebula-on");
  import("/static/hud/nebula.js?v=71").then((m) => {
    const center = document.getElementById("center");
    if (!center) return;
    const bg = document.createElement("div");
    bg.id = "nebula-bg";
    center.prepend(bg);
    SPAN._nebulaHandle = m.mount(bg, { authHeaders: SPAN.authHeaders });
    SPAN._nebulaHandle.setState(SPAN.state === "busy" ? "thinking" : (SPAN.state || "idle"));
    try {  // bewaarde orb-tuning meteen toepassen
      const t = JSON.parse(localStorage.getItem("span_nebula_orb") || "null");
      if (t) SPAN._nebulaHandle.setSettings(t);
      if (t && t.cinema === false) SPAN._nebulaHandle.setCinema(false);
      if (t && t.strak) SPAN._nebulaHandle.setNodeStyle("strak");
    } catch (e) { /* stil */ }
  }).catch((e) => console.warn("[nebula] laden mislukt:", e));
})();

/* -- start ----------------------------------------------------------------- */
boot();
// SSO-modus detecteren vóór we verbinden: bij web-login zonder sessie meteen
// naar de Microsoft-login; anders gewoon verbinden (token- of SSO-cookie).
// uitloggen (alleen zinvol in SSO-modus): cookie wissen -> Microsoft-login
$("logout-btn").onclick = () => { location.href = "/auth/logout"; };
fetch("/auth/status").then((r) => r.json()).then((s) => {
  SPAN.sso = !!s.web_login;
  // server-side neurale stem (Piper) gebruiken als beschikbaar; localStorage
  // "span_tts=browser" forceert de browser-stem
  SPAN.serverTTS = !!s.tts_available && localStorage.getItem("span_tts") !== "browser";
  SPAN.applyBranding(s.agent_name, s.agent_tagline);
  if (s.web_login && !s.authenticated) { location.href = "/auth/login"; return; }
  if (SPAN.sso) $("logout-btn").classList.remove("hidden");  // toon uitlog-knop
  connect();
}).catch(() => connect());
