/* SPAN ambient laag: Agent Inbox (goedkeuringswachtrij), toasts, health-dot. */
"use strict";
(() => {
  const SPAN = window.SPAN;
  const $ = (id) => document.getElementById(id);
  const overlay = $("inbox-overlay");
  let known = new Set();   // item-ids waarvoor al een toast is getoond
  let firstPoll = true;

  /* -- toasts ----------------------------------------------------------- */
  function toast(title, detail, urgency) {
    const box = $("toasts");
    const div = document.createElement("div");
    div.className = "toast" + (urgency === "high" ? " hot" : "");
    div.innerHTML = `<b>${esc(title)}</b><span>${esc(detail)}</span>`;
    div.onclick = () => { openInbox(); div.remove(); };
    box.appendChild(div);
    SPAN.chime(urgency === "high" ? 988 : 587, .09);
    // alert-takeover alleen bij injectie-waarschuwingen (glitch = signaal)
    if (detail && detail.includes("injectie") && SPAN.heroAlert) {
      SPAN.heroAlert(title, detail);
    }
    setTimeout(() => div.classList.add("gone"), 9000);
    setTimeout(() => div.remove(), 9600);
  }
  const esc = SPAN.esc;  // gedeelde escape-helper uit jarvis.js
  // M23: alleen http(s)-links renderen (weiger javascript:/data:-schema)
  const _safeHttp = (u) => {
    try { return /^https?:$/.test(new URL(u).protocol); } catch (e) { return false; }
  };

  /* -- inbox poll + badge ------------------------------------------------ */
  async function poll() {
    if (document.hidden) return;  // verborgen tab: server niet lastigvallen
    try {
      const res = await fetch("/api/inbox", { headers: SPAN.authHeaders() });
      if (!res.ok) return;
      const d = await res.json();
      $("inbox-badge").textContent = d.open || "";
      $("inbox-btn").classList.toggle("attention", d.open > 0);
      for (const item of d.items) {
        if (item.status === "open" && !known.has(item.id) && !firstPoll) {
          toast(item.title, item.detail, item.urgency);
        }
        known.add(item.id);
      }
      firstPoll = false;
      if (overlay.classList.contains("open")) render(d.items);
    } catch (e) { /* stil */ }
  }
  setInterval(poll, 20000);
  setTimeout(poll, 3000);

  /* -- inbox overlay ----------------------------------------------------- */
  const KIND_LABEL = { action: "ACTIE", needs_reply: "ANTWOORD NODIG", notify: "MELDING" };
  function render(items) {
    const list = $("inbox-list");
    list.innerHTML = "";
    const open = items.filter((i) => i.status === "open").reverse();
    const closed = items.filter((i) => i.status !== "open").reverse().slice(0, 8);
    if (!open.length) list.innerHTML = '<div class="empty">Niets te beoordelen — alles afgehandeld ✦</div>';
    for (const item of open) list.appendChild(card(item, true));
    if (closed.length) {
      const h = document.createElement("div");
      h.className = "inbox-divider"; h.textContent = "afgehandeld";
      list.appendChild(h);
      for (const item of closed) list.appendChild(card(item, false));
    }
  }
  // D: payload leesbaar tonen — je moet kunnen zíén wat je goedkeurt
  // (ontvanger, onderwerp, argumenten) i.p.v. alleen een titel.
  const PAYLOAD_LABEL = { to: "aan", subject: "onderwerp", body: "inhoud",
                          mcp_name: "tool", arguments: "argumenten",
                          action: "actie", start: "start", end: "einde" };
  function payloadRows(p) {
    if (!p || typeof p !== "object") return "";
    const rows = [];
    for (const [k, v] of Object.entries(p)) {
      if (k === "link" || v == null || v === "") continue;
      const val = typeof v === "object" ? JSON.stringify(v) : String(v);
      rows.push(`<div class="pl"><span>${esc(PAYLOAD_LABEL[k] || k)}</span>` +
                `${esc(val.length > 300 ? val.slice(0, 300) + "…" : val)}</div>`);
      if (rows.length >= 6) break;
    }
    return rows.length ? `<div class="inbox-payload">${rows.join("")}</div>` : "";
  }
  function card(item, open) {
    const div = document.createElement("div");
    div.className = "inbox-card" + (open ? "" : " closed") +
      (item.urgency === "high" ? " hot" : "");
    const approveLabel = item.kind === "needs_reply" ? "✍ concept maken"
      : item.kind === "notify" ? "✓ gezien" : "✓ goedkeuren";
    div.innerHTML =
      `<div class="k">${KIND_LABEL[item.kind] || item.kind} · ${esc(item.created.slice(11, 16))}` +
      `${item.status !== "open" ? " · " + esc(item.status) : ""}</div>` +
      `<b>${esc(item.title)}</b><p>${esc(item.detail)}</p>` +
      (open && item.kind === "action" ? payloadRows(item.payload) : "") +
      (item.payload && _safeHttp(item.payload.link)
        ? `<a href="${esc(item.payload.link)}" target="_blank" rel="noopener noreferrer">open in Outlook</a> ` : "");
    if (open) {
      const ok = document.createElement("button");
      ok.className = "ghost"; ok.textContent = approveLabel;
      ok.onclick = () => act(item.id, "approve", ok);
      const no = document.createElement("button");
      no.className = "ghost reject"; no.textContent = "✕ negeren";
      no.onclick = () => act(item.id, "reject", no);
      const row = document.createElement("div");
      row.className = "inbox-actions"; row.append(ok, no);
      div.appendChild(row);
    }
    return div;
  }
  async function act(id, verb, btn) {
    btn.disabled = true; btn.textContent = "…";
    try {
      const res = await fetch(`/api/inbox/${id}/${verb}`, {
        method: "POST", headers: SPAN.authHeaders(),
      });
      const d = await res.json();
      if (!res.ok) { SPAN.sys(d.detail || "Mislukt", "warn"); return; }
      if (verb === "approve") {
        SPAN.chime(880, .1);
        const r = d.result || {};
        if (r.draft_created) SPAN.sys("Concept staat klaar in Outlook Drafts.");
        else if (r.sent) SPAN.sys("Mail verstuurd.");
        else if (r.created) SPAN.sys("Afspraak staat in de agenda.");
      }
      poll();
    } catch (e) { SPAN.sys("Actie mislukt.", "warn"); }
  }

  function openInbox() { overlay.classList.add("open"); poll(); }
  $("inbox-btn").onclick = openInbox;
  $("inbox-close").onclick = () => overlay.classList.remove("open");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.remove("open");
  });

  /* -- achtergrondtaken: poll + paneel + meldingen ----------------------- */
  const tasksOverlay = $("tasks-overlay");
  const tasksDone = new Set();
  let tasksFirst = true;
  const T_STATUS = { queued: "in wachtrij", running: "bezig", cancelling: "annuleren…",
                     done: "klaar", error: "fout", cancelled: "geannuleerd",
                     interrupted: "onderbroken (herstart)" };
  async function pollTasks() {
    if (document.hidden) return;  // verborgen tab: server niet lastigvallen
    try {
      const res = await fetch("/api/tasks", { headers: SPAN.authHeaders() });
      if (!res.ok) return;
      const d = await res.json();
      $("tasks-badge").textContent = d.active || "";
      $("tasks-btn").classList.toggle("attention", d.active > 0);
      for (const t of d.tasks) {
        if (["done", "error", "cancelled"].includes(t.status) && !tasksDone.has(t.id)) {
          tasksDone.add(t.id);
          if (!tasksFirst) {
            toast("Taak " + (t.status === "done" ? "klaar" : t.status) + ": " + t.title,
                  (t.result || "").slice(0, 90), t.status === "error" ? "high" : "normal");
          }
        }
      }
      tasksFirst = false;
      if (tasksOverlay.classList.contains("open")) renderTasks(d.tasks);
    } catch (e) { /* stil */ }
  }
  setInterval(pollTasks, 4000);
  setTimeout(pollTasks, 3500);

  function renderTasks(items) {
    const list = $("tasks-list");
    list.innerHTML = "";
    if (!items.length) { list.innerHTML = '<div class="empty">Geen achtergrondtaken.</div>'; return; }
    for (const t of items) {
      const div = document.createElement("div");
      const active = ["queued", "running", "cancelling"].includes(t.status);
      div.className = "inbox-card" + (active ? "" : " closed") + (t.status === "error" ? " hot" : "");
      const pct = Math.max(0, Math.min(100, t.percent || 0));
      div.innerHTML =
        `<div class="k">TAAK #${t.id} · ${esc(T_STATUS[t.status] || t.status)}` +
        `${active ? " · " + pct + "%" : ""}${t.progress ? " · " + esc(t.progress) : ""}</div>` +
        `<b>${t.team ? "👥 " : ""}${esc(t.title)}</b>` +
        (active ? `<div class="taskbar"><span style="width:${pct}%"></span></div>` : "") +
        (t.result ? `<p>${esc((t.result || "").slice(0, 700))}</p>` : "");
      if (active) {
        const no = document.createElement("button");
        no.className = "ghost reject"; no.textContent = "✕ annuleren";
        no.onclick = async () => {
          no.disabled = true;
          await fetch(`/api/tasks/${t.id}/cancel`, { method: "POST", headers: SPAN.authHeaders() });
          pollTasks();
        };
        const row = document.createElement("div"); row.className = "inbox-actions"; row.append(no);
        div.appendChild(row);
      }
      list.appendChild(div);
    }
  }
  function openTasks() { tasksOverlay.classList.add("open"); pollTasks(); }
  $("tasks-btn").onclick = openTasks;
  $("tasks-close").onclick = () => tasksOverlay.classList.remove("open");
  tasksOverlay.addEventListener("click", (e) => {
    if (e.target === tasksOverlay) tasksOverlay.classList.remove("open");
  });

  /* -- health-dot -------------------------------------------------------- */
  async function healthPoll() {
    if (document.hidden) return;  // verborgen tab: server niet lastigvallen
    try {
      const res = await fetch("/api/health", { headers: SPAN.authHeaders() });
      const h = res.ok ? await res.json() : { brain: false };
      SPAN._health = h;
      if (SPAN._applyDot) SPAN._applyDot();  // gecombineerd met de WS-status
      if (!h.brain && SPAN.glitch) SPAN.glitch();
    } catch (e) {
      SPAN._health = { brain: false };
      if (SPAN._applyDot) SPAN._applyDot();
    }
  }
  setInterval(healthPoll, 60000);
  setTimeout(healthPoll, 4000);

  // tab weer zichtbaar -> meteen verversen i.p.v. wachten op de volgende tick
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { poll(); pollTasks(); healthPoll(); }
  });
})();
