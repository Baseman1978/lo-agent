/* SPAN instellingen-overlay: O365 koppelen/ontkoppelen, ORQ-modelkeuze. */
"use strict";
(() => {
  const SPAN = window.SPAN;
  const $ = (id) => document.getElementById(id);
  const overlay = $("settings-overlay");
  let defaults = null;

  function option(sel, value, current) {
    const o = document.createElement("option");
    o.value = value; o.textContent = value;
    if (value === current) o.selected = true;
    sel.appendChild(o);
  }

  async function load() {
    try {
      const [sRes, mRes] = await Promise.all([
        fetch("/api/settings", { headers: SPAN.authHeaders() }),
        fetch("/api/models", { headers: SPAN.authHeaders() }),
      ]);
      if (!sRes.ok) return;
      const s = await sRes.json();
      defaults = s.defaults;
      const models = mRes.ok ? (await mRes.json()).models : [s.model_main, s.model_light];

      $("set-o365-status").textContent = SPAN.sso
        ? `via app-login (SSO)${s.o365.account ? " — " + s.o365.account : ""} · uitloggen rechtsboven`
        : (s.o365.authenticated
            ? `gekoppeld: ${s.o365.account}`
            : (s.o365.configured ? "niet gekoppeld" : "niet geconfigureerd"));
      // in SSO-modus is de losse O365-koppeling overbodig (de app-login regelt het)
      $("o365-login").classList.toggle("hidden", SPAN.sso || s.o365.authenticated || !s.o365.configured);
      $("o365-logout").classList.toggle("hidden", SPAN.sso || !s.o365.authenticated);

      $("set-asana-status").textContent = s.asana.configured
        ? "gekoppeld (token in .env)"
        : "niet geconfigureerd — zet ASANA_TOKEN in .env";

      $("set-fireflies-status").textContent = s.fireflies && s.fireflies.configured
        ? "gekoppeld — meetings gaan elke 30 min het geheugen in, actiepunten naar de Agent Inbox"
        : "niet geconfigureerd — zet FIREFLIES_API_KEY in .env (Fireflies → Integrations → API)";

      $("set-telegram-status").textContent = !s.telegram || !s.telegram.configured
        ? "niet geconfigureerd — zet TELEGRAM_BOT_TOKEN in .env (bot via @BotFather)"
        : (s.telegram.linked
          ? "gekoppeld — " + (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO") + " stuurt je dagstart en antwoordt op berichten"
          : "bot actief — stuur hem: /koppel <SPAN_AUTH_TOKEN>");

      for (const [selId, current] of [["set-model-main", s.model_main],
        ["set-model-light", s.model_light]]) {
        const sel = $(selId); sel.innerHTML = "";
        const list = models.includes(current) ? models : [current, ...models];
        for (const m of list) option(sel, m, current);
      }

      if (s.briefing_time) $("set-briefing-time").value = s.briefing_time;
      if (s.tools) renderToolPerms(s.tools);
      const sp = $("set-sysprompt");
      if (sp && !sp.dataset.touched) {
        sp.value = s.system_prompt || s.system_prompt_default || "";
        sp.dataset.default = s.system_prompt_default || "";
      }
      if (typeof s.triage_rules === "string" && !$("set-triage").value) {
        $("set-triage").value = s.triage_rules;
      }
      if (s.autonomy) {
        $("set-auto-mail").value = s.autonomy.mail || "ask";
        $("set-auto-event").value = s.autonomy.event || "ask";
      }
      if (s.security) {
        $("sec-injection").checked = s.security.injection_scan !== false;
        $("sec-exfil").checked = s.security.exfil_guard !== false;
        $("sec-decay").value = s.security.decay_mode || "off";
        const bg = s.security.budget_iterations || 12;
        $("sec-budget").value = bg; $("sec-budget-label").textContent = bg;
      }

      if (!$("set-lan-ip").value) {
        const saved = localStorage.getItem("span_lan_ip");
        if (saved) { $("set-lan-ip").value = saved; }
        else {
          const nRes = await fetch("/api/netinfo", { headers: SPAN.authHeaders() });
          if (nRes.ok) {
            const n = await nRes.json();
            if (n.lan_ip) $("set-lan-ip").value = n.lan_ip;
            if (n.hint) $("qr-note").textContent = n.hint;
          }
        }
      }
    } catch (e) { /* stil */ }
  }

  $("settings-btn").onclick = () => { overlay.classList.add("open"); load(); loadMcp(); };
  $("settings-close").onclick = () => overlay.classList.remove("open");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.remove("open");
  });

  async function save(main, light) {
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ model_main: main, model_light: light }),
      });
      const d = await res.json();
      if (!res.ok) { SPAN.sys("Opslaan mislukt", "warn"); return; }
      SPAN.sys(`Modellen opgeslagen — hoofd: ${d.model_main} · licht: ${d.model_light}. ` +
        "Geldt voor nieuwe sessies (herlaad de pagina).");
      SPAN.chime(740, .1);
      load();
    } catch (e) { SPAN.sys("Opslaan mislukt.", "warn"); }
  }

  $("set-save").onclick = () =>
    save($("set-model-main").value, $("set-model-light").value);
  $("set-reset").onclick = () => {
    if (defaults) save(defaults.model_main, defaults.model_light);
  };

  /* -- dagstart ------------------------------------------------------- */
  $("set-briefing-save").onclick = async () => {
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ briefing_time: $("set-briefing-time").value }),
      });
      const d = await res.json();
      if (!res.ok) { SPAN.sys(d.detail || "Tijd opslaan mislukt", "warn"); return; }
      SPAN.sys(`Dagstart staat op ${d.briefing_time} — elke ochtend klaar.`);
      SPAN.chime(740, .1);
    } catch (e) { SPAN.sys("Tijd opslaan mislukt.", "warn"); }
  };
  $("set-briefing-now").onclick = async () => {
    overlay.classList.remove("open");
    SPAN.sys("Dagstart genereren…");
    if (SPAN.playDaily) SPAN.playDaily(true);
  };

  /* -- QR-code: Span op je telefoon ------------------------------------ */
  $("set-qr-make").onclick = () => {
    const ip = $("set-lan-ip").value.trim();
    if (!ip) { $("qr-note").textContent = "Vul eerst het LAN-IP van deze pc in (ipconfig)."; return; }
    localStorage.setItem("span_lan_ip", ip);
    const url = `http://${ip}:8472/?token=${encodeURIComponent(localStorage.getItem("span_token") || "")}`;
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    const box = $("qr-box");
    box.innerHTML = qr.createImgTag(5, 8);
    box.classList.remove("hidden");
    $("qr-note").textContent = `Scan met je telefoon (zelfde wifi): ${url}`;
  };

  /* -- stem ------------------------------------------------------------- */
  function fillVoices() {
    const sel = $("set-voice");
    if (!window.SPAN.nlVoices) return;
    const names = SPAN.nlVoices();
    if (!names.length) return;
    sel.innerHTML = "";
    const saved = localStorage.getItem("span_voice") || "";
    for (const n of names) option(sel, n, saved || names[0]);
  }
  fillVoices();
  setTimeout(fillVoices, 1500);  // stemmen laden async
  $("set-voice").onchange = () => {
    localStorage.setItem("span_voice", $("set-voice").value);
    if (SPAN.repickVoice) SPAN.repickVoice();
    if (SPAN.speak) SPAN.speak("Zo klink ik.");
  };
  const rate = $("set-rate");
  rate.value = localStorage.getItem("span_rate") || "1.04";
  $("set-rate-label").textContent = rate.value;
  rate.oninput = () => {
    $("set-rate-label").textContent = rate.value;
    localStorage.setItem("span_rate", rate.value);
  };

  /* -- triage-regels ------------------------------------------------------ */
  $("set-triage-save").onclick = async () => {
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ triage_rules: $("set-triage").value }),
      });
      if (!res.ok) { SPAN.sys("Regels opslaan mislukt", "warn"); return; }
      SPAN.sys("Triage-regels opgeslagen — de watcher volgt ze direct.");
      SPAN.chime(740, .1);
    } catch (e) { SPAN.sys("Regels opslaan mislukt.", "warn"); }
  };

  /* -- backup -------------------------------------------------------------- */
  $("set-backup").onclick = async () => {
    try {
      const res = await fetch("/api/backup", { headers: SPAN.authHeaders() });
      if (!res.ok) { SPAN.sys("Backup mislukt", "warn"); return; }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "span-brein-backup.json";
      a.click();
      URL.revokeObjectURL(a.href);
      SPAN.sys("Brein-backup gedownload.");
    } catch (e) { SPAN.sys("Backup mislukt.", "warn"); }
  };

  /* -- autonomie -------------------------------------------------------- */
  $("set-auto-save").onclick = async () => {
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ autonomy: {
          mail: $("set-auto-mail").value, event: $("set-auto-event").value,
        }}),
      });
      if (!res.ok) { SPAN.sys("Autonomie opslaan mislukt", "warn"); return; }
      SPAN.sys("Autonomie-instellingen opgeslagen.");
      SPAN.chime(740, .1);
    } catch (e) { SPAN.sys("Autonomie opslaan mislukt.", "warn"); }
  };

  /* -- MCP-servers ---------------------------------------------------------- */
  async function loadMcp() {
    const box = $("mcp-list");
    if (!box) return;
    try {
      const res = await fetch("/api/mcp", { headers: SPAN.authHeaders() });
      if (!res.ok) return;
      const d = await res.json();
      box.innerHTML = "";
      if (!d.servers.length) { box.textContent = "nog geen servers gekoppeld"; return; }
      for (const s of d.servers) {
        const row = document.createElement("div");
        row.className = "setrow";
        const status = s.connected ? "✅ verbonden"
          : (s.logged_in ? "ingelogd (herstart voor tools)" : "niet ingelogd");
        const label = document.createElement("span");
        label.className = "m"; label.textContent = `${s.name} — ${status}`;
        const connect = document.createElement("button");
        connect.className = "ghost"; connect.textContent = s.logged_in ? "opnieuw inloggen" : "inloggen";
        connect.onclick = () => mcpConnect(s.name);
        const del = document.createElement("button");
        del.className = "ghost"; del.textContent = "✕";
        del.onclick = () => mcpDelete(s.name);
        row.append(label, connect, del);
        box.appendChild(row);
      }
    } catch (e) { /* stil */ }
  }
  async function mcpConnect(name) {
    try {
      const res = await fetch(`/api/mcp/${encodeURIComponent(name)}/connect`,
        { method: "POST", headers: SPAN.authHeaders() });
      const d = await res.json();
      if (!res.ok) { SPAN.sys(d.detail || "OAuth starten mislukt", "warn"); return; }
      // I6: authorize_url komt deels uit untrusted MCP-metadata — alleen https
      // openen (weiger javascript:/data: -> geen XSS in de Span-origin)
      let u;
      try { u = new URL(d.authorize_url); } catch (e) { u = null; }
      if (!u || u.protocol !== "https:") {
        SPAN.sys("Login-URL geweigerd (geen geldige https-URL).", "warn");
        return;
      }
      SPAN.sys(`Open de login voor '${name}' in je browser…`);
      window.open(u.href, "_blank", "noopener,noreferrer");
    } catch (e) { SPAN.sys("OAuth starten mislukt.", "warn"); }
  }
  async function mcpDelete(name) {
    if (!confirm(`MCP-server '${name}' verwijderen?`)) return;
    await fetch(`/api/mcp/${encodeURIComponent(name)}`,
      { method: "DELETE", headers: SPAN.authHeaders() });
    loadMcp();
  }
  const _bind = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  _bind("mcp-add", async () => {
    const name = $("mcp-name").value.trim(), url = $("mcp-url").value.trim();
    if (!name || !url) { SPAN.sys("Naam en URL invullen.", "warn"); return; }
    const res = await fetch("/api/mcp", {
      method: "POST",
      headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ name, url }),
    });
    if (!res.ok) { const d = await res.json(); SPAN.sys(d.detail || "Toevoegen mislukt", "warn"); return; }
    $("mcp-name").value = ""; $("mcp-url").value = "";
    SPAN.sys(`MCP-server '${name}' toegevoegd — klik 'inloggen' om te koppelen.`);
    loadMcp();
  });

  /* -- beveiliging ---------------------------------------------------------- */
  { const bg = $("sec-budget"); if (bg) bg.addEventListener("input", () => {
    $("sec-budget-label").textContent = bg.value; }); }
  _bind("sec-save", async () => {
    const inj = $("sec-injection").checked, exf = $("sec-exfil").checked;
    if ((!inj || !exf) && !confirm(
        "Je zet een bescherming UIT. " +
        (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO") +
        " is dan kwetsbaarder voor misleiding " +
        "via mail of een datalek. Zeker weten?")) return;
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ security: {
          injection_scan: inj, exfil_guard: exf,
          decay_mode: $("sec-decay").value,
          budget_iterations: parseInt($("sec-budget").value),
        }}),
      });
      if (!res.ok) { SPAN.sys("Beveiliging opslaan mislukt", "warn"); return; }
      SPAN.sys("Beveiligingsinstellingen opgeslagen (geldt voor nieuwe sessies).");
      SPAN.chime(740, .1);
    } catch (e) { SPAN.sys("Beveiliging opslaan mislukt.", "warn"); }
  });

  /* -- systeemprompt -------------------------------------------------------- */
  const spArea = $("set-sysprompt");
  if (spArea) spArea.addEventListener("input", () => { spArea.dataset.touched = "1"; });
  async function saveSysPrompt(value) {
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: value }),
      });
      const d = await res.json();
      if (!res.ok) { SPAN.sys("Prompt opslaan mislukt", "warn"); return; }
      SPAN.sys(d.custom
        ? "Eigen systeemprompt opgeslagen — geldt voor nieuwe sessies (herlaad)."
        : "Systeemprompt terug naar de ingebouwde standaard.");
      SPAN.chime(740, .1);
      delete spArea.dataset.touched;
      load();
    } catch (e) { SPAN.sys("Prompt opslaan mislukt.", "warn"); }
  }
  $("set-sysprompt-save").onclick = () => {
    const v = spArea.value.trim();
    if (v && !v.includes("{bootstrap}")) {
      SPAN.sys("Let op: {bootstrap} ontbreekt — zonder die plekhouder verliest " +
        (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO") +
        " zijn geheugen-context. Voeg hem toe en sla opnieuw op.", "warn");
      return;
    }
    saveSysPrompt(v === spArea.dataset.default ? "" : v);
  };
  $("set-sysprompt-reset").onclick = () => { spArea.value = spArea.dataset.default || ""; saveSysPrompt(""); };

  /* -- tool-permissies ----------------------------------------------------- */
  function renderToolPerms(tools) {
    const box = $("tool-perms");
    box.innerHTML = "";
    const groups = {};
    for (const t of tools) (groups[t.group] = groups[t.group] || []).push(t);
    for (const [group, items] of Object.entries(groups)) {
      // createElement i.p.v. innerHTML: serverdata hoort nooit als markup geparsed
      const g = document.createElement("div");
      g.className = "perm-group" + (items[0].available ? "" : " unavailable");
      const title = document.createElement("div");
      title.className = "perm-title";
      title.textContent = group + (items[0].available ? "" : " · niet gekoppeld");
      g.appendChild(title);
      for (const t of items) {
        const row = document.createElement("label");
        row.className = "perm-row";
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.dataset.tool = t.name; cb.checked = !!t.enabled;
        const badge = document.createElement("span");
        badge.className = "perm-badge " + (t.access === "read" ? "read" : "write");
        badge.textContent = t.access === "read" ? "R" : "W";
        const name = document.createElement("span");
        name.className = "perm-name"; name.textContent = t.name;
        row.append(cb, badge, name);
        g.appendChild(row);
      }
      box.appendChild(g);
    }
  }
  $("set-tools-save").onclick = async () => {
    const disabled = [...document.querySelectorAll("#tool-perms input:not(:checked)")]
      .map((i) => i.dataset.tool);
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ disabled_tools: disabled }),
      });
      if (!res.ok) { SPAN.sys("Permissies opslaan mislukt", "warn"); return; }
      SPAN.sys(`Tool-permissies opgeslagen — ${disabled.length} geblokkeerd. ` +
        "Geldt voor nieuwe sessies (herlaad de pagina).");
      SPAN.chime(740, .1);
    } catch (e) { SPAN.sys("Permissies opslaan mislukt.", "warn"); }
  };

  /* -- effect-intensiteit (#100) ------------------------------------------ */
  const fxSel = $("set-fx");
  fxSel.value = localStorage.getItem("span_fx") ?? "2";
  fxSel.onchange = () => {
    localStorage.setItem("span_fx", fxSel.value);
    if (window.SPANFX) window.SPANFX.level = parseInt(fxSel.value);
    SPAN.chime(660, .08);
  };

  /* -- thema ------------------------------------------------------------- */
  const themeSel = $("set-theme");
  const savedTheme = localStorage.getItem("span_theme") || "";
  document.body.dataset.theme = savedTheme;
  themeSel.value = savedTheme;
  themeSel.onchange = () => {
    document.body.dataset.theme = themeSel.value;
    localStorage.setItem("span_theme", themeSel.value);
    SPAN.chime(660, .08);
  };

  /* -- command palette (Ctrl+K) ------------------------------------------ */
  const COMMANDS = [
    ["Agent Inbox openen", () => document.getElementById("inbox-btn").click()],
    ["Instellingen openen", () => { overlay.classList.add("open"); load(); }],
    ["Brein-hologram fullscreen", () => document.getElementById("holo-expand").click()],
    ["Dagstart afspelen", () => SPAN.playDaily && SPAN.playDaily(true)],
    ["Wake word aan/uit", () => document.getElementById("wake").click()],
    ["Voorlezen aan/uit", () => document.getElementById("speak").click()],
    ["Sessie afsluiten (/end)", () => document.getElementById("end").click()],
    ["Thema: Arc Blue", () => { themeSel.value = ""; themeSel.onchange(); }],
    ["Thema: Mark III", () => { themeSel.value = "mark3"; themeSel.onchange(); }],
    ["Thema: War Machine", () => { themeSel.value = "warmachine"; themeSel.onchange(); }],
  ];
  const pal = document.createElement("div");
  pal.id = "palette";
  pal.innerHTML = '<input id="palette-q" placeholder="Typ een commando…"><div id="palette-list"></div>';
  document.body.appendChild(pal);
  const palQ = pal.querySelector("#palette-q"), palList = pal.querySelector("#palette-list");
  let palIdx = 0;
  function palRender() {
    const q = palQ.value.toLowerCase();
    const hits = COMMANDS.filter(([name]) => name.toLowerCase().includes(q));
    palList.innerHTML = "";
    hits.forEach(([name, fn], i) => {
      const div = document.createElement("div");
      div.className = "palette-item" + (i === palIdx ? " sel" : "");
      div.textContent = name;
      div.onclick = () => { fn(); palClose(); };
      palList.appendChild(div);
    });
    return hits;
  }
  function palClose() { pal.classList.remove("open"); palQ.value = ""; palIdx = 0; }
  addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      pal.classList.toggle("open");
      if (pal.classList.contains("open")) { palIdx = 0; palRender(); palQ.focus(); }
    } else if (pal.classList.contains("open")) {
      if (e.key === "Escape") palClose();
      else if (e.key === "ArrowDown") {
        const count = palRender().length;
        palIdx = Math.min(palIdx + 1, Math.max(0, count - 1));
        palRender(); e.preventDefault();
      }
      else if (e.key === "ArrowUp") { palIdx = Math.max(0, palIdx - 1); palRender(); e.preventDefault(); }
      else if (e.key === "Enter") {
        const hits = palRender();
        const hit = hits[Math.min(palIdx, hits.length - 1)];
        if (hit) { hit[1](); palClose(); }
      }
    }
  });
  palQ.addEventListener("input", () => { palIdx = 0; palRender(); });

  /* palet ook via de header-knop (touch heeft geen Ctrl+K) */
  const palBtn = document.getElementById("palette-btn");
  if (palBtn) palBtn.onclick = () => {
    pal.classList.toggle("open");
    if (pal.classList.contains("open")) { palIdx = 0; palRender(); palQ.focus(); }
  };

  /* -- Orb (centrale visual): live tweaken, lokaal bewaard ----------------- */
  function orbInit() {
    if (!SPAN.orbConfig) return;       // orb.js niet geladen (geen three.js)
    const cfg = SPAN.orbConfig();
    const set = (id, v) => { const el = $(id); if (el) el.value = v; };
    const lbl = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set("orb-style", cfg.style); set("orb-palette", cfg.palette); set("orb-shape", cfg.shape);
    set("orb-cubes", cfg.cubes); lbl("orb-cubes-label", cfg.cubes);
    set("orb-pulse", cfg.pulse); lbl("orb-pulse-label", cfg.pulse.toFixed(1));
    set("orb-rot", cfg.rotation); lbl("orb-rot-label", cfg.rotation.toFixed(1));
    set("orb-size", cfg.cubeSize); lbl("orb-size-label", cfg.cubeSize);
    set("orb-smooth", cfg.smooth); lbl("orb-smooth-label", (cfg.smooth || 0.25).toFixed(2));
    set("orb-bloom", cfg.bloom); lbl("orb-bloom-label", (cfg.bloom == null ? 1.6 : cfg.bloom).toFixed(1));
    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("input", fn); };
    on("orb-style", (e) => SPAN.applyOrbConfig({ style: e.target.value }));
    on("orb-shape", (e) => SPAN.applyOrbConfig({ shape: e.target.value }));
    on("orb-palette", (e) => SPAN.applyOrbConfig({ palette: e.target.value }));
    on("orb-cubes", (e) => { lbl("orb-cubes-label", e.target.value); SPAN.applyOrbConfig({ cubes: parseInt(e.target.value) }); });
    on("orb-pulse", (e) => { lbl("orb-pulse-label", (+e.target.value).toFixed(1)); SPAN.applyOrbConfig({ pulse: +e.target.value }); });
    on("orb-rot", (e) => { lbl("orb-rot-label", (+e.target.value).toFixed(1)); SPAN.applyOrbConfig({ rotation: +e.target.value }); });
    on("orb-size", (e) => { lbl("orb-size-label", e.target.value); SPAN.applyOrbConfig({ cubeSize: +e.target.value }); });
    on("orb-smooth", (e) => { lbl("orb-smooth-label", (+e.target.value).toFixed(2)); SPAN.applyOrbConfig({ smooth: +e.target.value }); });
    on("orb-bloom", (e) => { lbl("orb-bloom-label", (+e.target.value).toFixed(1)); SPAN.applyOrbConfig({ bloom: +e.target.value }); });
    const rst = $("orb-reset");
    if (rst) rst.onclick = () => {
      SPAN.applyOrbConfig({ style:"orb", shape:"bol", cubes:1200, pulse:1.0, rotation:1.0, cubeSize:0.05, radius:2.0, palette:"span", smooth:0.25, bloom:1.6 });
      orbInit();
    };
  }
  orbInit();

  /* -- Stem (server-TTS): live tweaken, lokaal bewaard -------------------- */
  function ttsInit() {
    const wrap = $("tts-settings");
    if (!wrap) return;
    const g = (k, d) => { const v = localStorage.getItem(k); return v === null ? d : v; };
    const set = (id, v) => { const el = $(id); if (el) el.value = v; };
    const lbl = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set("tts-length", g("span_tts_length", "1.0")); lbl("tts-length-label", (+g("span_tts_length", "1.0")).toFixed(2));
    set("tts-noise", g("span_tts_noise", "0.667")); lbl("tts-noise-label", (+g("span_tts_noise", "0.667")).toFixed(2));
    set("tts-noisew", g("span_tts_noisew", "0.8")); lbl("tts-noisew-label", (+g("span_tts_noisew", "0.8")).toFixed(2));
    set("tts-volume", g("span_tts_volume", "1.0")); lbl("tts-volume-label", (+g("span_tts_volume", "1.0")).toFixed(2));
    fetch("/api/tts/status", { headers: SPAN.authHeaders() }).then((r) => r.json()).then((s) => {
      if (!s.available) { wrap.style.display = "none"; return; }
      const sel = $("tts-speaker");
      if (sel && s.num_speakers > 1) {
        sel.innerHTML = "";
        for (let i = 0; i < s.num_speakers; i++) {
          const o = document.createElement("option"); o.value = i; o.textContent = "stem " + i; sel.appendChild(o);
        }
        sel.value = g("span_tts_speaker", "0");
      } else if (sel) { const row = sel.closest(".setrow"); if (row) row.style.display = "none"; }
    }).catch(() => {});
    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("input", fn); };
    on("tts-speaker", (e) => localStorage.setItem("span_tts_speaker", e.target.value));
    on("tts-length", (e) => { lbl("tts-length-label", (+e.target.value).toFixed(2)); localStorage.setItem("span_tts_length", e.target.value); });
    on("tts-noise", (e) => { lbl("tts-noise-label", (+e.target.value).toFixed(2)); localStorage.setItem("span_tts_noise", e.target.value); });
    on("tts-noisew", (e) => { lbl("tts-noisew-label", (+e.target.value).toFixed(2)); localStorage.setItem("span_tts_noisew", e.target.value); });
    on("tts-volume", (e) => { lbl("tts-volume-label", (+e.target.value).toFixed(2)); localStorage.setItem("span_tts_volume", e.target.value); });
    const test = $("tts-test"); if (test) test.onclick = () => { if (SPAN.ttsSample) SPAN.ttsSample(); };
    const rst = $("tts-reset"); if (rst) rst.onclick = () => {
      ["span_tts_speaker", "span_tts_length", "span_tts_noise", "span_tts_noisew", "span_tts_volume"]
        .forEach((k) => localStorage.removeItem(k));
      ttsInit();
    };
  }
  ttsInit();

  /* statusje in settings live houden wanneer o365 net (ont)koppeld is */
  window.addEventListener("focus", () => {
    if (overlay.classList.contains("open")) load();
  });
  SPAN.refreshSettings = load;
})();
