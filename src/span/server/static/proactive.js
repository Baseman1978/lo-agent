/* PROACTIEF SPREKEN — LO zegt een paar spreekwaardige meldingen (dagafsluiting,
   weekreview, meeting-prep, urgente mail) HARDOP, maar ALLEEN als het moment
   veilig is. Anders wacht de melding in de server-queue (/api/announcements).

   Vijf voorwaarden moeten groen zijn (gepolld elke paar seconden):
     1. Online   — tabblad zichtbaar (document.visibilityState === "visible")
     2. Aanwezig — niet idle/locked: Idle Detection API waar beschikbaar +
                   toestemming, anders in-tab-activiteit (mouse/keys, laatste 3 min)
     3. Geen call— geen aanhoudende mic-spraak (VAD, SPAN._recentSpeech) én geen
                   agenda-meeting met andere genodigden (/api/presence/meeting_now)
     4. DND uit  — 'niet storen'-schakelaar staat uit / de timer is verlopen
     5. LO stil  — SPAN.state niet speaking/busy en Bas typt niet

   De feature staat default UIT (vraagt mic-toegang); Bas zet 'm bewust aan via
   Instellingen → Agent. Server-side quiet-hours houden de queue leeg tijdens de
   stille uren, dus daar zwijgt LO vanzelf. */
"use strict";
(() => {
  const SPAN = window.SPAN;
  if (!SPAN) return;
  const $ = (id) => document.getElementById(id);

  const LS_FEATURE = "span_proactive";      // "1" = aan
  const LS_DND = "span_dnd_until";           // "" | "inf" | einde-epoch-ms
  const POLL_MS = 5000;                      // trigger-lus + queue-poll
  const PRESENT_WINDOW_MS = 3 * 60 * 1000;   // in-tab-activiteit: laatste 3 min
  const MEETING_TTL_MS = 30000;              // agenda-check hooguit elke 30s
  const SPEAK_GAP_MS = 1500;                 // korte pauze na het spreken

  const featureOn = () => localStorage.getItem(LS_FEATURE) === "1";

  /* -- 2. aanwezigheid: Idle Detection API, val terug op in-tab-activiteit -- */
  let lastActivity = Date.now();
  ["mousemove", "keydown", "pointerdown", "touchstart", "wheel", "scroll"].forEach((ev) =>
    addEventListener(ev, () => { lastActivity = Date.now(); }, { passive: true }));

  let idleReady = false, idleUserActive = true, screenLocked = false, idleDetector = null;
  async function startIdleDetector() {
    if (idleReady || !("IdleDetector" in window)) return;
    try {
      const perm = await IdleDetector.requestPermission();
      if (perm !== "granted") return;   // val terug op in-tab-activiteit
      idleDetector = new IdleDetector();
      idleDetector.addEventListener("change", () => {
        idleUserActive = idleDetector.userState === "active";
        screenLocked = idleDetector.screenState === "locked";
      });
      await idleDetector.start({ threshold: 60000 });  // API-minimum is 60s
      idleReady = true;
    } catch (e) { /* geen OS-idle: in-tab-activiteit blijft het vangnet */ }
  }
  function present() {
    if (idleReady) return idleUserActive && !screenLocked;
    return (Date.now() - lastActivity) < PRESENT_WINDOW_MS;
  }

  /* -- 4. niet storen (DND) met timer ----------------------------------- */
  function dndActive() {
    const raw = localStorage.getItem(LS_DND);
    if (!raw) return false;
    if (raw === "inf") return true;
    const end = parseInt(raw, 10);
    if (isNaN(end)) return false;
    if (Date.now() >= end) { localStorage.removeItem(LS_DND); return false; }
    return true;
  }
  function dndRemainingLabel() {
    const raw = localStorage.getItem(LS_DND);
    if (raw === "inf") return "∞";
    const end = parseInt(raw, 10);
    if (isNaN(end) || Date.now() >= end) return "";
    return Math.ceil((end - Date.now()) / 60000) + "m";
  }
  function setDnd(spec) {  // spec: minuten (number), "inf", of null=uit
    if (spec === null) localStorage.removeItem(LS_DND);
    else if (spec === "inf") localStorage.setItem(LS_DND, "inf");
    else localStorage.setItem(LS_DND, String(Date.now() + spec * 60000));
    syncDndButton();
  }
  SPAN._dndActive = dndActive;  // deelbaar met andere lagen indien nodig

  /* -- DND-knop + klein menu (15/30/60 min of 'tot uit') ---------------- */
  const dndBtn = $("dnd-btn");
  let dndMenu = null;
  function syncDndButton() {
    if (!dndBtn) return;
    dndBtn.classList.toggle("hidden", !featureOn());
    const on = dndActive();
    dndBtn.classList.toggle("active", on);
    const badge = $("dnd-badge");
    if (badge) badge.textContent = on ? dndRemainingLabel() : "";
    dndBtn.title = on
      ? "Niet storen actief (" + (dndRemainingLabel() || "aan") + ") — klik om te wijzigen"
      : "Niet storen — LO zwijgt (proactief spreken)";
  }
  function closeDndMenu() {
    if (dndMenu) { dndMenu.remove(); dndMenu = null; }
    removeEventListener("click", onDocClick, true);
  }
  function onDocClick(e) {
    if (dndMenu && !dndMenu.contains(e.target) && e.target !== dndBtn) closeDndMenu();
  }
  function openDndMenu() {
    closeDndMenu();
    dndMenu = document.createElement("div");
    dndMenu.className = "panel";
    dndMenu.style.cssText = "position:fixed;z-index:60;padding:8px;min-width:180px;"
      + "display:flex;flex-direction:column;gap:6px";
    const r = dndBtn.getBoundingClientRect();
    dndMenu.style.top = (r.bottom + 6) + "px";
    dndMenu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
    const mk = (label, spec) => {
      const b = document.createElement("button");
      b.className = "ghost"; b.textContent = label;
      b.onclick = () => {
        setDnd(spec);
        SPAN.sys(spec === null ? "Niet storen uit — LO mag weer spreken."
          : "Niet storen aan" + (spec === "inf" ? " (tot je hem uitzet)." : " voor " + spec + " min."));
        closeDndMenu();
      };
      return b;
    };
    const title = document.createElement("div");
    title.className = "m"; title.style.opacity = ".7"; title.textContent = "Niet storen";
    dndMenu.appendChild(title);
    dndMenu.appendChild(mk("15 minuten", 15));
    dndMenu.appendChild(mk("30 minuten", 30));
    dndMenu.appendChild(mk("60 minuten", 60));
    dndMenu.appendChild(mk("tot ik hem uitzet", "inf"));
    if (dndActive()) dndMenu.appendChild(mk("nu uitzetten", null));
    document.body.appendChild(dndMenu);
    setTimeout(() => addEventListener("click", onDocClick, true), 0);
  }
  if (dndBtn) dndBtn.onclick = () => { dndMenu ? closeDndMenu() : openDndMenu(); };
  // badge-tijd laten aftellen zodat 'm' klopt
  setInterval(syncDndButton, 15000);

  /* -- 3b. agenda-aanwezigheid (server, fail-stil naar niet-blokkerend) --- */
  let meetingBlocking = false, meetingCheckedAt = 0;
  async function refreshMeeting() {
    if (Date.now() - meetingCheckedAt < MEETING_TTL_MS) return;
    meetingCheckedAt = Date.now();
    try {
      const r = await fetch("/api/presence/meeting_now", { headers: SPAN.authHeaders() });
      if (r.ok) meetingBlocking = !!(await r.json()).blocking;
      else meetingBlocking = false;
    } catch (e) { meetingBlocking = false; }
  }

  /* -- 5. LO/Bas bezig --------------------------------------------------- */
  function typing() {
    const el = document.activeElement;
    return !!(el && el.id === "input" && el.value && el.value.trim());
  }
  function loBusy() {
    return SPAN.state === "speaking" || SPAN.state === "busy" || SPAN.busy;
  }

  function allGreen() {
    if (!featureOn()) return false;
    if (document.visibilityState !== "visible") return false;          // 1
    if (!present()) return false;                                       // 2
    if (SPAN._recentSpeech && SPAN._recentSpeech()) return false;       // 3a
    if (meetingBlocking) return false;                                  // 3b
    if (dndActive()) return false;                                      // 4
    if (loBusy() || typing()) return false;                            // 5
    return true;
  }

  /* -- spreken: lees het item volledig voor via de bestaande TTS -------- */
  function startSpeak(text) {
    if (!SPAN.speak) return;
    const prevOn = SPAN.speakOn;
    SPAN.speakOn = true;          // dit item mag klinken, ook als 🔊 uit stond
    SPAN.speak(text, true);       // full=true: geen lengte-cap (briefing-modus)
    SPAN.speakOn = prevOn;        // gebruikersvoorkeur voor gewone antwoorden herstellen
  }
  function waitUntilQuiet() {     // pacing: wachten tot LO klaar is met praten
    return new Promise((resolve) => {
      const start = Date.now();
      const iv = setInterval(() => {
        const settled = SPAN.state !== "speaking" && Date.now() - start > 1200;
        if (settled || Date.now() - start > 90000) { clearInterval(iv); resolve(); }
      }, 300);
    });
  }

  /* -- de trigger-lus ---------------------------------------------------- */
  let speaking = false;
  async function tick() {
    if (!featureOn() || speaking) return;
    await refreshMeeting();
    if (!allGreen()) return;
    let item;
    try {
      const r = await fetch("/api/announcements", { headers: SPAN.authHeaders() });
      if (!r.ok) return;
      item = ((await r.json()).items || [])[0];   // één tegelijk
    } catch (e) { return; }
    if (!item) return;
    if (!allGreen()) return;      // her-check vlak vóór het spreken
    speaking = true;
    try {
      startSpeak(item.text);
      // meteen als uitgesproken markeren -> nooit twee keer voorlezen
      await fetch("/api/announcements/" + item.id + "/spoken",
        { method: "POST", headers: SPAN.authHeaders() });
      await waitUntilQuiet();
    } catch (e) { /* stil */ }
    setTimeout(() => { speaking = false; }, SPEAK_GAP_MS);
  }
  setInterval(tick, POLL_MS);

  /* -- aan/uit vanuit de instellingen ------------------------------------ */
  SPAN.proactive = {
    async setEnabled(on) {
      if (on) {
        // mic pas hier openen (nette toestemming), hergebruik een bestaande stream
        const micOk = SPAN.ensureMicSensing ? await SPAN.ensureMicSensing() : false;
        if (!micOk) return false;           // geen mic -> feature niet aanzetten
        localStorage.setItem(LS_FEATURE, "1");
        startIdleDetector();                 // best effort (OS-brede idle/lock)
        syncDndButton();
        return true;
      }
      localStorage.setItem(LS_FEATURE, "0");
      syncDndButton();
      return true;
    },
  };

  // bij het laden: stond de feature al aan, meteen weer opstarten (mic-toestemming
  // is dan doorgaans al verleend, dus geen nieuwe prompt). Faalt zacht.
  if (featureOn()) {
    if (SPAN.ensureMicSensing) SPAN.ensureMicSensing().catch(() => {});
    startIdleDetector();
  }
  syncDndButton();
})();
