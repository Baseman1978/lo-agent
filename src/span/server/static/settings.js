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

  // C6: medewerkers zien alleen hun persoonlijke tabs (Skills/Stem/Uiterlijk);
  // de beheer-tabs (Integraties/Agent/Systeem) zijn voor de owner. Dit is een
  // UI-schakel — de schrijfroutes zitten server-side al achter _require_owner.
  const ADMIN_TABS = ["integraties", "agent", "systeem"];
  function applyRole(isOwner) {
    if (isOwner !== false) return;  // owner, single-user of oudere backend: alles tonen
    document.querySelectorAll(".settab-btn, .settab").forEach((n) => {
      if (ADMIN_TABS.includes(n.dataset.tab)) n.classList.add("hidden");
    });
    const active = document.querySelector(".settab-btn.active");
    if (active && ADMIN_TABS.includes(active.dataset.tab)) {
      const first = document.querySelector('.settab-btn[data-tab="stem"]')
        || document.querySelector(".settab-btn:not(.hidden)");
      if (first) first.click();
    }
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
      applyRole(s.is_owner);
      const models = mRes.ok ? (await mRes.json()).models : [s.model_main, s.model_light];

      $("set-o365-status").textContent = SPAN.sso
        ? `via app-login (SSO)${s.o365.account ? " — " + s.o365.account : ""} · uitloggen rechtsboven`
        : (s.o365.authenticated
            ? `gekoppeld: ${s.o365.account}`
            : (s.o365.configured ? "niet gekoppeld" : "niet geconfigureerd"));
      // in SSO-modus is de losse O365-koppeling overbodig (de app-login regelt het)
      $("o365-login").classList.toggle("hidden", SPAN.sso || s.o365.authenticated || !s.o365.configured);
      $("o365-logout").classList.toggle("hidden", SPAN.sso || !s.o365.authenticated);

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

  /* -- sub-tabs in het instellingen-paneel -------------------------------- */
  (function tabsInit() {
    const btns = document.querySelectorAll(".settab-btn");
    const panes = document.querySelectorAll(".settab");
    if (!btns.length) return;
    const show = (name) => {
      btns.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
      panes.forEach((p) => p.classList.toggle("active", p.dataset.tab === name));
      const panel = $("settings-panel"); if (panel) panel.scrollTop = 0;
    };
    btns.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  })();

  /* -- Stem (server-TTS): live tweaken, lokaal bewaard -------------------- */
  function ttsInit() {
    const wrap = $("tts-settings");
    if (!wrap) return;
    const g = (k, d) => { const v = localStorage.getItem(k); return v === null ? String(d) : v; };
    const set = (id, v) => { const el = $(id); if (el) el.value = v; };
    const lbl = (id, v) => { const el = $(id); if (el) el.textContent = (+v).toFixed(2); };
    const showRow = (id, show) => { const el = $(id), row = el && el.closest(".setrow"); if (row) row.style.display = show ? "" : "none"; };
    const SLIDERS = ["tts-length", "tts-noise", "tts-noisew", "tts-volume"];
    fetch("/api/tts/status", { headers: SPAN.authHeaders() }).then((r) => r.json()).then((s) => {
      if (!s.available) { wrap.style.display = "none"; return; }
      SPAN._ttsStreaming = !!s.streaming;   // XTTS streamt -> lage latency
      const sel = $("tts-speaker");
      if (s.engine === "xtts") {
        // XTTS: stem = naam, geen Piper-schuiven
        SLIDERS.forEach((id) => showRow(id, false));
        if (sel) {
          const names = s.speakers || [];
          sel.innerHTML = "";
          names.forEach((n) => { const o = document.createElement("option"); o.value = n; o.textContent = n; sel.appendChild(o); });
          const stored = localStorage.getItem("span_tts_speaker");
          sel.value = (stored && names.includes(stored)) ? stored : (s.default_speaker || names[0] || "");
          showRow("tts-speaker", names.length > 1);
        }
      } else {
        // Piper: nummers + schuiven; defaults = modelstandaard van de server
        SLIDERS.forEach((id) => showRow(id, true));
        const dLen = s.model_length != null ? s.model_length : 1.0;
        const dNoise = s.model_noise != null ? s.model_noise : 0.667;
        const dNoiseW = s.model_noisew != null ? s.model_noisew : 0.8;
        set("tts-length", g("span_tts_length", dLen)); lbl("tts-length-label", g("span_tts_length", dLen));
        set("tts-noise", g("span_tts_noise", dNoise)); lbl("tts-noise-label", g("span_tts_noise", dNoise));
        set("tts-noisew", g("span_tts_noisew", dNoiseW)); lbl("tts-noisew-label", g("span_tts_noisew", dNoiseW));
        set("tts-volume", g("span_tts_volume", 1.0)); lbl("tts-volume-label", g("span_tts_volume", 1.0));
        if (sel && s.num_speakers > 1) {
          sel.innerHTML = "";
          for (let i = 0; i < s.num_speakers; i++) { const o = document.createElement("option"); o.value = i; o.textContent = "stem " + i; sel.appendChild(o); }
          sel.value = g("span_tts_speaker", "0");
          showRow("tts-speaker", true);
        } else { showRow("tts-speaker", false); }
      }
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

  /* -- Skills: lijst + maken/bewerken/aan-uit/verwijderen ------------------ */
  function skillsInit() {
    const listEl = $("skills-list");
    if (!listEl) return;
    const kindSel = $("sk-kind");
    const toggleKind = () => {
      const macro = kindSel.value === "macro";
      $("sk-body-row").classList.toggle("hidden", macro);
      $("sk-steps-row").classList.toggle("hidden", !macro);
      $("sk-params-row").classList.toggle("hidden", !macro);
    };
    const clearForm = () => {
      ["sk-name", "sk-desc", "sk-trigger", "sk-body", "sk-steps", "sk-params"]
        .forEach((id) => { const el = $(id); if (el) el.value = ""; });
      kindSel.value = "workflow"; toggleKind();
    };
    const fillForm = (s) => {
      $("sk-name").value = s.name || ""; $("sk-desc").value = s.description || "";
      $("sk-trigger").value = s.trigger || ""; kindSel.value = s.kind || "workflow";
      $("sk-body").value = s.body || "";
      $("sk-steps").value = (s.steps && s.steps.length) ? JSON.stringify(s.steps, null, 2) : "";
      $("sk-params").value = (s.params || []).join(", ");
      toggleKind();
    };
    async function load() {
      try {
        const d = await (await fetch("/api/skills", { headers: SPAN.authHeaders() })).json();
        $("sk-tools").textContent = "Beschikbare tools: " + (d.tools || []).join(", ");
        const skills = d.skills || [];
        if (!skills.length) { listEl.innerHTML = '<div class="empty">nog geen skills</div>'; return; }
        listEl.innerHTML = "";
        skills.forEach((s) => {
          const row = document.createElement("div");
          row.style.cssText = "display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--line)";
          const tag = s.kind === "macro" ? "⚙ macro" : "werkwijze";
          const who = s.author === "agent" ? " · door LO" : "";
          const lab = document.createElement("label");
          lab.style.cssText = "flex:1;cursor:pointer";
          lab.innerHTML = `<input type="checkbox" ${s.enabled ? "checked" : ""}> <b>${s.name}</b> <span style="opacity:.6">· ${tag}${who}</span><br><span style="opacity:.7;font-size:11px">${s.description || ""}</span>`;
          lab.querySelector("input").onchange = (e) =>
            fetch("/api/skills/" + encodeURIComponent(s.name) + "/enable", {
              method: "POST", headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
              body: JSON.stringify({ enabled: e.target.checked }) });
          const ed = document.createElement("button"); ed.className = "iconbtn"; ed.textContent = "✎"; ed.title = "bewerken";
          ed.onclick = () => fillForm(s);
          const del = document.createElement("button"); del.className = "iconbtn"; del.textContent = "✕"; del.title = "verwijderen";
          del.onclick = async () => {
            if (!confirm("Skill '" + s.name + "' verwijderen?")) return;
            await fetch("/api/skills/" + encodeURIComponent(s.name), { method: "DELETE", headers: SPAN.authHeaders() });
            load();
          };
          row.appendChild(lab); row.appendChild(ed); row.appendChild(del);
          listEl.appendChild(row);
        });
      } catch (e) { listEl.textContent = "kon skills niet laden"; }
    }
    kindSel.addEventListener("change", toggleKind);
    $("sk-save").onclick = async () => {
      const kind = kindSel.value;
      const payload = { name: $("sk-name").value.trim(), description: $("sk-desc").value.trim(),
                        trigger: $("sk-trigger").value.trim(), kind, enabled: true };
      if (kind === "workflow") { payload.body = $("sk-body").value; }
      else {
        try { payload.steps = JSON.parse($("sk-steps").value || "[]"); }
        catch (e) { SPAN.sys("Stappen-JSON is ongeldig.", "warn"); return; }
        payload.params = $("sk-params").value.split(",").map((x) => x.trim()).filter(Boolean);
      }
      const res = await fetch("/api/skills", { method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); SPAN.sys("Opslaan mislukt: " + (d.detail || res.status), "warn"); return; }
      SPAN.sys("Skill opgeslagen."); clearForm(); load();
    };
    $("sk-clear").onclick = clearForm;
    toggleKind(); load();
  }
  skillsInit();

  /* -- Integraties: catalogus (Integration Broker) ------------------------ */
  function integrationsInit() {
    const catEl = $("int-catalog"); if (!catEl) return;
    const detailEl = $("int-detail"), searchEl = $("int-search"), catSel = $("int-category");
    let all = [];
    const STATUS = { available: "beschikbaar", needs_config: "config nodig", beta: "beta", planned: "gepland" };
    const badge = (t, c) => `<span class="int-badge ${c || ""}">${t}</span>`;

    function render() {
      const q = (searchEl.value || "").toLowerCase().trim(), cat = catSel.value;
      const items = all.filter((c) => (!cat || c.category === cat) &&
        (!q || (c.name + " " + c.id + " " + (c.summary || "")).toLowerCase().includes(q)));
      if (!items.length) { catEl.innerHTML = '<div class="empty">geen apps</div>'; return; }
      catEl.innerHTML = "";
      items.forEach((c) => {
        const card = document.createElement("div"); card.className = "int-card";
        const conn = c.connected ? badge("✓ gekoppeld", "ok") : "";
        const st = c.status !== "available"
          ? badge(STATUS[c.status] || c.status, c.status === "needs_config" ? "warn" : "") : "";
        card.innerHTML =
          `<div class="int-head"><b>${c.name}</b> <span class="int-cat">${c.category}</span></div>
           <div class="int-sum">${c.summary || ""}</div>
           <div class="int-meta">${conn}${st}${badge("risk: " + c.risk, c.risk === "high" ? "warn" : "")}${badge(c.action_count + " acties")}</div>`;
        card.onclick = () => openDetail(c);
        catEl.appendChild(card);
      });
    }

    async function openDetail(c) {
      detailEl.classList.remove("hidden"); detailEl.innerHTML = "laden…";
      let d = {};
      try { d = await (await fetch("/api/integrations/" + encodeURIComponent(c.id) + "/actions", { headers: SPAN.authHeaders() })).json(); }
      catch (e) { detailEl.textContent = "kon acties niet laden"; return; }
      const acts = d.actions || [];
      const rows = acts.map((a) => {
        const ap = a.approval === "never" ? "direct" : "goedkeuring";
        const runBtn = (c.provider === "mock" && a.approval === "never")
          ? `<button class="ghost int-run" data-c="${c.id}" data-a="${a.id}">uitvoeren</button>` : "";
        return `<div class="int-act"><div><b>${a.name}</b> <span class="int-cap">${a.capability} · ${ap}</span><br>
                <span class="int-desc">${a.description || ""}</span></div>${runBtn}</div>`;
      }).join("") || '<div class="m" style="opacity:.7">nog geen acties gedefinieerd</div>';
      let connect = "";
      if (c.auth === "api_key") {
        const link = c.key_url ? ` · <a href="${c.key_url}" target="_blank" rel="noopener">sleutel ophalen</a>` : "";
        connect = `<div class="int-key">
          <input type="password" id="int-key-input" autocomplete="off" spellcheck="false"
            placeholder="${c.connected ? "nieuwe sleutel (leeg = ongewijzigd)" : "plak je API-sleutel"}">
          <button class="ghost" id="int-key-save">${c.connected ? "vervangen" : "opslaan"}</button>
          ${c.connected ? '<button class="ghost" id="int-key-remove">verwijderen</button>' : ""}
          <div class="m" style="opacity:.7;margin-top:4px">${c.connected ? "✓ sleutel ingesteld" : "De sleutel wordt server-side bewaard — nooit in de frontend of de prompt."}${link}</div>
        </div>`;
      } else if (!c.connected) {
        if (c.provider === "mcp" && c.mcp_url) connect = `<button class="ghost" id="int-connect">Koppelen (login)</button>`;
        else if (c.status === "needs_config") connect = `<div class="m" style="opacity:.7">Deze koppeling vereist nog configuratie.</div>`;
      }
      detailEl.innerHTML =
        `<div class="int-detail-head"><b>${c.name}</b> <button class="iconbtn" id="int-detail-close">✕</button></div>
         <div class="m" style="opacity:.7;margin-bottom:6px">${c.summary || ""}${c.docs_url ? ` · <a href="${c.docs_url}" target="_blank" rel="noopener">docs</a>` : ""}</div>
         ${connect}<div class="int-acts">${rows}</div>`;
      $("int-detail-close").onclick = () => detailEl.classList.add("hidden");
      const cbtn = $("int-connect");
      if (cbtn) cbtn.onclick = async () => {
        const reset = () => { cbtn.disabled = false; cbtn.textContent = "Koppelen (login)"; };
        cbtn.disabled = true; cbtn.textContent = "koppelen…";
        try {
          // 1) registreer de MCP-server (idempotent) 2) start OAuth 3) open login
          const add = await fetch("/api/mcp", { method: "POST",
            headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({ name: c.id, url: c.mcp_url }) });
          if (!add.ok) { const d = await add.json().catch(() => ({})); SPAN.sys(d.detail || "Toevoegen mislukt", "warn"); return reset(); }
          const res = await fetch(`/api/mcp/${encodeURIComponent(c.id)}/connect`, { method: "POST", headers: SPAN.authHeaders() });
          const d = await res.json().catch(() => ({}));
          if (!res.ok) { SPAN.sys(d.detail || "Koppelen mislukt", "warn"); return reset(); }
          let u; try { u = new URL(d.authorize_url); } catch (e) { u = null; }
          if (!u || u.protocol !== "https:") { SPAN.sys("Login-URL geweigerd (geen https).", "warn"); return reset(); }
          SPAN.sys(`Open de login voor ${c.name} in je browser…`);
          window.open(u.href, "_blank", "noopener,noreferrer");
          reset();
        } catch (e) { SPAN.sys("Koppelen mislukt", "warn"); reset(); }
      };
      const ksave = $("int-key-save");
      if (ksave) ksave.onclick = async () => {
        const val = (($("int-key-input") || {}).value || "").trim();
        if (!val) { SPAN.sys("Vul een sleutel in.", "warn"); return; }
        ksave.disabled = true; ksave.textContent = "…";
        try {
          const r = await fetch(`/api/integrations/${encodeURIComponent(c.id)}/key`, {
            method: "POST", headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({ key: val }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) { SPAN.sys(d.detail || "Opslaan mislukt", "warn"); ksave.disabled = false; ksave.textContent = c.connected ? "vervangen" : "opslaan"; return; }
          SPAN.sys(`${c.name} ${d.connected ? "gekoppeld" : "sleutel opgeslagen"}.`);
          load(); openDetail({ ...c, connected: d.connected });
        } catch (e) { SPAN.sys("Opslaan mislukt", "warn"); ksave.disabled = false; }
      };
      const krem = $("int-key-remove");
      if (krem) krem.onclick = async () => {
        if (!confirm(`Sleutel van ${c.name} verwijderen?`)) return;
        try {
          await fetch(`/api/integrations/${encodeURIComponent(c.id)}/key`, { method: "DELETE", headers: SPAN.authHeaders() });
          SPAN.sys(`Sleutel van ${c.name} verwijderd.`);
          load(); openDetail({ ...c, connected: false });
        } catch (e) { SPAN.sys("Verwijderen mislukt", "warn"); }
      };
      detailEl.querySelectorAll(".int-run").forEach((b) => b.onclick = async () => {
        b.disabled = true; b.textContent = "…";
        try {
          const r = await (await fetch(`/api/integrations/${encodeURIComponent(b.dataset.c)}/${encodeURIComponent(b.dataset.a)}/run`,
            { method: "POST", headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" }, body: "{}" })).json();
          SPAN.sys("Resultaat: " + JSON.stringify(r.result != null ? r.result : r));
        } catch (e) { SPAN.sys("Uitvoeren mislukt", "warn"); }
        b.disabled = false; b.textContent = "uitvoeren";
      });
    }

    async function load() {
      try {
        const d = await (await fetch("/api/integrations/catalog", { headers: SPAN.authHeaders() })).json();
        all = d.connectors || [];
        const cats = [...new Set(all.map((c) => c.category))].sort();
        catSel.innerHTML = '<option value="">alle categorieën</option>' +
          cats.map((c) => `<option value="${c}">${c}</option>`).join("");
        render();
      } catch (e) { catEl.textContent = "kon catalogus niet laden"; }
    }
    searchEl.addEventListener("input", render);
    catSel.addEventListener("change", render);
    const intTab = document.querySelector('.settab-btn[data-tab="integraties"]');
    if (intTab) intTab.addEventListener("click", load);   // ververs status na login
    load();
  }
  integrationsInit();

  /* statusje in settings live houden wanneer o365 net (ont)koppeld is */
  window.addEventListener("focus", () => {
    if (overlay.classList.contains("open")) load();
  });
  SPAN.refreshSettings = load;
})();
