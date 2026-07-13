/* SPAN spraaklaag: TTS, push-to-talk, fuzzy wake word, hot window
   (doorpraten zonder wake word), barge-in ("stop") en mic-level → fx. */
"use strict";
(() => {
  const SPAN = window.SPAN;
  const $ = (id) => document.getElementById(id);

  /* -- fuzzy matching (wake word + echo + stopwoorden) ---------------------- */
  function similarity(a, b) {
    a = a.toLowerCase(); b = b.toLowerCase();
    if (a === b) return 1;
    const big = (s) => {
      const out = new Set();
      for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2));
      return out;
    };
    const A = big(a), B = big(b);
    if (!A.size || !B.size) return 0;
    let hits = 0;
    for (const x of A) if (B.has(x)) hits++;
    return (2 * hits) / (A.size + B.size);
  }
  // wake-word = de agentnaam (configureerbaar via AGENT_NAME). Korte namen
  // (≤3 tekens, bv. "LO") krijgen een strenge drempel tegen vals alarm.
  const STOP_WORDS = ["stop", "stil", "genoeg", "kappen", "stilte"];
  const wakeWord = () => (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO").toLowerCase();

  // Een korte naam als "LO" (2 tekens) hoort de browser onbetrouwbaar: er komt
  // "low", "loo", "loh", "lo" of "loe" uit. Match daarom soepel op fonetische
  // varianten i.p.v. een strenge bigram-drempel.
  function wakeMatch(word, wake) {
    if (!word) return false;
    if (word === wake) return true;
    if (wake.length <= 3) {
      // begint met de naam, hooguit 2 tekens langer (low/loo/loh/lows/loe)
      if (word.startsWith(wake) && word.length <= wake.length + 2) return true;
      // of de naam begint met het gehoorde woord (bv. "l" -> "lo")
      if (wake.startsWith(word)) return true;
      return similarity(word, wake) >= 0.8;
    }
    return similarity(word, wake) >= 0.62;
  }
  function findWake(transcript) {
    const wake = wakeWord();
    const words = transcript.toLowerCase().replace(/[.,!?;:]/g, "").split(/\s+/);
    for (let i = 0; i < words.length; i++) {
      if (wakeMatch(words[i], wake)) return words.slice(i + 1).join(" ").trim();
    }
    return null;
  }
  const isStop = (transcript) =>
    transcript.toLowerCase().split(/\s+/).some((w) =>
      STOP_WORDS.some((s) => similarity(w, s) >= 0.8));

  /* -- TTS: streaming per zin, stemprofiel uit instellingen ----------------- */
  let voice = null, lastTTS = "";
  function pickVoice() {
    const all = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("nl"));
    const wanted = localStorage.getItem("span_voice");
    voice = (wanted && all.find((v) => v.name === wanted))
      || all.find((v) => /natural|online/i.test(v.name)) || all[0] || null;
  }
  if ("speechSynthesis" in window) {
    speechSynthesis.onvoiceschanged = pickVoice; pickVoice();
  }
  SPAN.repickVoice = pickVoice;
  SPAN.nlVoices = () => speechSynthesis.getVoices()
    .filter((v) => v.lang.startsWith("nl")).map((v) => v.name);

  /* -- server-side TTS (Piper) via WebAudio: één heldere stem i.p.v. de
        wisselende browser-stemmen. Aan als de server tts_available meldt
        (jarvis.js boot); valt anders terug op SpeechSynthesis. Speelt per zin
        af in een wachtrij; barge-in stopt de bron en breekt de fetch af. ----- */
  SPAN.serverTTS = false;
  let _ax = null, ttsQ = [], ttsPlaying = false, curSrc = null, ttsAbort = null, ttsLast = false;
  let ttsAnalyser = null, ttsLevelData = null;
  SPAN._ttsLevel = 0;   // niveau van LO's eigen stem (voor echo-bewuste barge-in)
  function audioCtx() {
    if (!_ax) _ax = new (window.AudioContext || window.webkitAudioContext)();
    if (_ax.state === "suspended") _ax.resume();
    return _ax;
  }
  // de TTS speelt via een analyser zodat de mic-loop weet hoe hard LO nu praat
  function ttsMonitor() {
    const ax = audioCtx();
    if (!ttsAnalyser) {
      ttsAnalyser = ax.createAnalyser();
      ttsAnalyser.fftSize = 256;
      ttsAnalyser.connect(ax.destination);
      ttsLevelData = new Uint8Array(ttsAnalyser.frequencyBinCount);
    }
    return ttsAnalyser;
  }
  SPAN._ttsStreaming = false;   // XTTS streamt? (gezet uit /api/tts/status)
  let streamSources = [];
  let ttsHead = 0, ttsFinishT = null;   // gedeelde afspeel-tijdlijn (gap-loos)
  const ttsIdleServer = () => ttsQ.length === 0 && !ttsPlaying;
  function ttsStop() {
    ttsQ = []; ttsLast = false;
    if (ttsFinishT) { clearTimeout(ttsFinishT); ttsFinishT = null; }
    if (ttsAbort) { try { ttsAbort.abort(); } catch (e) {} ttsAbort = null; }
    if (curSrc) { try { curSrc.onended = null; curSrc.stop(); } catch (e) {} curSrc = null; }
    streamSources.forEach((n) => { try { n.onended = null; n.stop(); } catch (e) {} });
    streamSources = [];
    ttsHead = 0;
    ttsPlaying = false;
  }

  // batch: hele zin als WAV ophalen, decoderen, afspelen (Piper of fallback)
  function ttsPlayBatch(text) {
    return new Promise((resolve, reject) => {
      ttsAbort = new AbortController();
      fetch("/api/tts", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ text, ...ttsParams() }),
        signal: ttsAbort.signal,
      }).then((res) => {
        ttsAbort = null;
        if (!res.ok) throw new Error("tts " + res.status);
        return res.arrayBuffer();
      }).then((arr) => {
        if (SPAN._muteStream) return resolve();
        return audioCtx().decodeAudioData(arr).then((audio) => {
          if (SPAN._muteStream) return resolve();
          const src = audioCtx().createBufferSource();
          src.buffer = audio; src.connect(ttsMonitor());
          curSrc = src;
          SPAN.setState("speaking");
          src.onended = () => { curSrc = null; resolve(); };
          src.start();
        });
      }).catch(reject);
    });
  }

  // stream: ruwe PCM16 @24k binnenkrijgen en per brok inplannen -> ~0,2s tot klank
  async function ttsPlayStream(text) {
    ttsAbort = new AbortController();
    const res = await fetch("/api/tts_stream", {
      method: "POST",
      headers: { ...SPAN.authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ text, ...ttsParams() }),
      signal: ttsAbort.signal,
    });
    ttsAbort = null;
    if (!res.ok || !res.body) throw new Error("stream " + res.status);
    const sr = parseInt(res.headers.get("X-Sample-Rate") || "24000", 10);
    const ax = audioCtx();
    const reader = res.body.getReader();
    let got = false, leftover = new Uint8Array(0);
    while (true) {
      let r;
      try { r = await reader.read(); } catch (e) { break; }
      if (r.done) break;
      if (SPAN._muteStream) { try { reader.cancel(); } catch (e) {} break; }
      let value = r.value; if (!value || !value.length) continue;
      let bytes = value;
      if (leftover.length) {
        bytes = new Uint8Array(leftover.length + value.length);
        bytes.set(leftover); bytes.set(value, leftover.length);
      }
      const n = bytes.length - (bytes.length % 2);
      if (n < 2) { leftover = bytes.slice(0); continue; }
      const aligned = bytes.slice(0, n);          // vers + 2-uitgelijnd
      leftover = bytes.slice(n);
      const samples = new Int16Array(aligned.buffer);
      const f32 = new Float32Array(samples.length);
      for (let i = 0; i < samples.length; i++) f32[i] = samples[i] / 32768;
      const buf = ax.createBuffer(1, f32.length, sr);
      buf.copyToChannel(f32, 0);
      const node = ax.createBufferSource();
      node.buffer = buf; node.connect(ttsMonitor());
      // gedeelde tijdlijn: plan achteraan de vorige (zin)brokken -> gap-loos
      if (ttsHead < ax.currentTime + 0.02) { ttsHead = ax.currentTime + 0.08; SPAN.setState("speaking"); }
      node.start(ttsHead);
      ttsHead += buf.duration;
      streamSources.push(node);
      node.onended = () => { const k = streamSources.indexOf(node); if (k >= 0) streamSources.splice(k, 1); };
      got = true;
    }
    if (!got) throw new Error("lege stream");   // -> batch-fallback
    // klaar zodra de GENERATIE klaar is (niet de weergave) zodat de volgende
    // zin alvast rendert terwijl deze nog speelt
  }

  function ttsFinish() {
    if (SPAN.state === "speaking") SPAN.setState("idle");
    if (ttsLast) { ttsLast = false; openHotWindow(); }
    ttsHead = 0;
  }
  async function ttsPump() {
    if (ttsPlaying) return;
    const item = ttsQ.shift();
    if (!item) {
      // streaming: de generatie is klaar maar er kan nog audio in de tijdlijn
      // staan -> pas afronden als die is uitgespeeld
      const ax = _ax;
      if (ax && ttsHead > ax.currentTime + 0.02) {
        if (ttsFinishT) clearTimeout(ttsFinishT);
        ttsFinishT = setTimeout(() => {
          if (ttsQ.length === 0 && !ttsPlaying) ttsFinish();
        }, (ttsHead - ax.currentTime) * 1000 + 80);
      } else {
        ttsFinish();
      }
      return;
    }
    if (ttsFinishT) { clearTimeout(ttsFinishT); ttsFinishT = null; }
    ttsPlaying = true;
    try {
      if (SPAN.serverTTS && SPAN._ttsStreaming) await ttsPlayStream(item.text);
      else await ttsPlayBatch(item.text);
    } catch (e) {
      if (e && e.name === "AbortError") { ttsPlaying = false; return; }  // barge-in
      // streaming faalde -> probeer dezelfde zin als batch
      if (SPAN.serverTTS && SPAN._ttsStreaming && !SPAN._muteStream) {
        try { await ttsPlayBatch(item.text); } catch (e2) {}
      }
    }
    ttsPlaying = false;
    ttsPump();
  }
  function ttsEnqueue(text, last) {
    if (last) ttsLast = true;
    const t = (text || "").trim();
    if (t) ttsQ.push({ text: t });
    ttsPump();
  }
  // A2 — micro-bevestiging: korte gesproken cue als een tool-call lang duurt,
  // zodat hoorbaar is dat LO nog bezig is. Alleen bij server-TTS, alleen als
  // voorlezen aanstaat, de spraak-pijplijn idle is en er geen barge-in loopt;
  // hoogstens één cue per beurt (SPAN._ackSpoken; jarvis.js reset bij "done").
  // Vaste korte frasen: het echo-filter (lastTTS) moet ze kunnen herkennen.
  const ACK_PHRASES = ["Momentje.", "Ik ben ermee bezig.", "Even geduld."];
  SPAN._ackSpoken = false;
  SPAN.microAck = () => {
    if (!SPAN.serverTTS || !SPAN.speakOn) return;
    if (SPAN._muteStream || SPAN._ackSpoken) return;
    if (!ttsIdleServer()) return;   // nooit door het echte antwoord heen praten
    const phrase = ACK_PHRASES[Math.floor(Math.random() * ACK_PHRASES.length)];
    SPAN._ackSpoken = true;
    lastTTS += " " + phrase;        // echo-filter rekent de cue mee
    ttsEnqueue(phrase, false);
  };
  // stem-instellingen (HUD) die per call meegaan naar /api/tts
  function ttsParams() {
    const p = {}, g = (k) => localStorage.getItem(k);
    const sp = g("span_tts_speaker");
    if (sp !== null && sp !== "") p.speaker = sp;  // naam (XTTS) of nummer (Piper)
    if (g("span_tts_length")) p.length_scale = parseFloat(g("span_tts_length"));
    if (g("span_tts_noise")) p.noise_scale = parseFloat(g("span_tts_noise"));
    if (g("span_tts_noisew")) p.noise_w_scale = parseFloat(g("span_tts_noisew"));
    if (g("span_tts_volume")) p.volume = parseFloat(g("span_tts_volume"));
    return p;
  }
  SPAN._ttsParams = ttsParams;
  // sample voorlezen met de huidige instellingen (testknop in instellingen).
  // Een hele alinea met variatie (begroeting, vraag, getal, doorlopende zin)
  // zodat je intonatie, tempo en expressie goed kunt beoordelen.
  const TTS_SAMPLE =
    "Goedemiddag Bas. Ik ben LO, de digitale assistent van Lomans. " +
    "Zo klink ik wanneer ik je voorlees: je agenda, je mail en de belangrijkste " +
    "punten van de dag. Je hebt vandaag drie afspraken; de eerste begint om half tien. " +
    "Zal ik de notulen van gisteren er even bij pakken? Laat het me weten — ik luister mee.";
  SPAN.ttsSample = (text) => {
    ttsStop(); SPAN._muteStream = false;
    ttsEnqueue(text || TTS_SAMPLE, true);
  };

  let queueOpen = 0;        // utterances onderweg
  let spokenChars = 0;      // spraak-cap per antwoord: niet álles voorlezen
  let capAnnounced = false;
  const SPEAK_CAP = 420;

  function speakable(text) {
    // lijsten, tabellen en kopjes niet voorlezen — alleen lopende zinnen
    return text.split("\n")
      .filter((l) => !/^\s*([-*•|]|\d+[.)]|#)/.test(l))
      .join(" ");
  }

  function speakChunk(text, last) {
    let clean = speakable(text).replace(/```[\s\S]*?```/g, " ")
      .replace(/[*#`_|]/g, "").trim();
    if (spokenChars >= SPEAK_CAP) {
      if (!capAnnounced) { capAnnounced = true; clean = "De rest staat in beeld."; }
      else clean = "";
    } else if (spokenChars + clean.length > SPEAK_CAP + 200) {
      // ruim over de cap: kap af op zinsgrens
      const cut = clean.slice(0, SPEAK_CAP).lastIndexOf(".");
      clean = clean.slice(0, cut > 60 ? cut + 1 : SPEAK_CAP) + " De rest staat in beeld.";
      capAnnounced = true;
      spokenChars = SPEAK_CAP;
    }
    spokenChars += clean.length;
    if (!clean) { if (last) openHotWindowSoon(); return; }
    lastTTS += " " + clean;
    if (SPAN.serverTTS) { ttsEnqueue(clean, last); return; }  // Piper via WebAudio
    const u = new SpeechSynthesisUtterance(clean);
    u.lang = "nl-NL"; if (voice) u.voice = voice;
    u.rate = parseFloat(localStorage.getItem("span_rate") || "1.04");
    queueOpen++;
    u.onstart = () => SPAN.setState("speaking");
    u.onend = () => {
      queueOpen--;
      if (queueOpen <= 0) {
        if (SPAN.state === "speaking") SPAN.setState("idle");
        if (last) openHotWindow();  // wake-modus herstart zichzelf via onend
      }
    };
    speechSynthesis.speak(u);  // queued: zinnen sluiten op elkaar aan
  }
  const openHotWindowSoon = () => setTimeout(openHotWindow, 100);

  SPAN.speak = (text, full) => {
    lastTTS = "";
    if (SPAN.serverTTS) ttsStop(); else speechSynthesis.cancel();
    queueOpen = 0;
    spokenChars = full ? -100000 : 0;  // full: geen cap (dagstart, briefing)
    capAnnounced = false;
    SPAN._muteStream = false;          // expliciete speak (briefing) mag klinken
    speakChunk(text, true);
  };

  /* streaming: zinnen voorlezen terwijl het antwoord nog binnenstroomt.
     Een naïeve splitser breekt op elke punt — ook na afkortingen ("bijv.",
     "d.w.z.") en in getallen ("3.14", "1.000") — en dat hakkelt mid-zin.
     sentenceEnd() breekt daarom alleen op een écht zin-eind: leesteken +
     witruimte + een nieuwe zin (hoofdletter/cijfer/aanhaling), en nooit vlak
     na een bekende afkorting of tussen cijfers. */
  const TTS_ABBREV = new Set([
    "bijv", "bijvb", "ca", "nr", "o.a", "d.w.z", "dwz", "z.g", "zg", "ing",
    "dr", "ir", "mr", "drs", "prof", "tel", "resp", "incl", "excl", "max",
    "min", "evt", "enz", "etc", "blz", "pag", "vgl", "t.o.v", "n.a.v",
    "i.v.m", "i.p.v", "m.b.t", "a.s", "z.s.m", "jl", "pers",
  ]);
  function sentenceEnd(buf) {
    // vroegste echte zinsgrens; retourneert de te knippen lengte, of -1
    const re = /[.!?]/g;
    let m;
    while ((m = re.exec(buf)) !== null) {
      const i = m.index;
      const after = buf.slice(i + 1);
      const sp = after.match(/^([ \t\n]+)(\S)?/);
      if (!sp) continue;                 // geen witruimte -> geen grens (3.14)
      if (sp[2] === undefined) return -1; // nog niets ná de spatie -> wachten
      if (buf[i] === ".") {
        const before = buf.slice(0, i).match(/(\S+)$/);
        const tok = before ? before[1].toLowerCase().replace(/[^a-z.]/g, "") : "";
        if (TTS_ABBREV.has(tok) || TTS_ABBREV.has(tok.replace(/\.+$/, ""))) continue;
        if (/\d$/.test(buf.slice(0, i)) && /^\d/.test(after)) continue;  // getal
      }
      // een nieuwe zin begint met hoofdletter, cijfer of aanhaling
      if (!/[A-ZÀ-ÖØ-Þ0-9"'(\[]/.test(sp[2])) continue;
      return i + 1 + sp[1].length;
    }
    return -1;
  }
  let streamBuf = "", firstChunk = true;
  SPAN.speakDelta = (delta) => {
    if (!SPAN.speakOn || SPAN._muteStream) return;
    if (streamBuf === "" && (SPAN.serverTTS ? ttsIdleServer() : queueOpen === 0)) {
      lastTTS = "";
      if (SPAN.serverTTS) ttsStop(); else speechSynthesis.cancel();
      spokenChars = 0; capAnnounced = false;
      firstChunk = true;   // nieuw antwoord -> eerste fragment snel laten klinken
    }
    streamBuf += delta;
    const minLen = firstChunk ? 14 : 30;
    let cut = sentenceEnd(streamBuf);
    if (cut < minLen) cut = -1;
    if (firstChunk) {
      // eerste fragment ook op een komma/zinsdeel breken zodat het geluid eerder
      // begint (XTTS rendert ~evenredig met de lengte); daarna per hele zin
      const early = streamBuf.match(/^[\s\S]*?[,;:]\s/);
      if (early && early[0].length >= minLen && (cut === -1 || early[0].length < cut))
        cut = early[0].length;
    }
    if (cut >= minLen) {
      speakChunk(streamBuf.slice(0, cut), false);
      firstChunk = false;
      streamBuf = streamBuf.slice(cut);
    }
  };
  SPAN.speakFlush = () => {
    const rest = streamBuf; streamBuf = "";
    speakChunk(rest, true);
  };
  const stopSpeaking = () => {
    speechSynthesis.cancel();
    if (SPAN.serverTTS) ttsStop();
    queueOpen = 0; streamBuf = "";
    // mute resterende stream-delta's van de afgebroken beurt: ze mogen de TTS
    // niet opnieuw starten. Wordt opgeheven bij de volgende beurt/het volgende
    // antwoord (SPAN.send en de done-afhandeling).
    SPAN._muteStream = true;
    if (SPAN.state === "speaking") SPAN.setState("idle");
  };

  /* -- barge-in: de gebruiker begint te praten terwijl de agent praat of nadenkt.
        Stop de TTS meteen en, als er een beurt loopt, breek die op de server af
        (acoustische onderbreking — niet wachten op een stopwoord). --------------- */
  let barged = false;
  function onBargeIn() {
    stopSpeaking();
    if (SPAN.busy && SPAN.cancel) {
      SPAN.cancel();                 // server: breek de lopende beurt af
      SPAN.sys("· onderbroken — ik luister ·");
    }
    if (mode !== "off") SPAN.setState("listening");
  }

  $("speak").classList.add("active");
  $("speak").onclick = (e) => {
    SPAN.speakOn = !SPAN.speakOn;
    e.target.classList.toggle("active", SPAN.speakOn);
    if (!SPAN.speakOn) stopSpeaking();
  };

  /* -- mic level → fx (audio-reactieve reactor/particles) -------------------- */
  let micCtx = null, analyser = null, micData = null, timeData = null, micStream = null;
  async function initMicLevel() {
    if (analyser) return;
    try {
      // echo-onderdrukking: cruciaal voor barge-in zodat de mic de eigen TTS
      // niet als 'gebruiker praat' terughoort
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const stream = micStream;
      micCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = micCtx.createMediaStreamSource(stream);
      analyser = micCtx.createAnalyser();
      analyser.fftSize = 1024;  // time-domein detail voor de oscilloscoop
      src.connect(analyser);   // analyser hoeft niet naar destination (MDN)
      micData = new Uint8Array(analyser.frequencyBinCount);
      timeData = new Uint8Array(analyser.fftSize);
      const BARGE_FRAMES = 12;  // ~200 ms aanhoudende, duidelijke stem
      let bargeFrames = 0;
      let noiseFloor = 0.02;    // lopende schatting van de omgevingsruis
      SPAN._recentPeak = 0;     // vervalt in ~0,5s: "was er net nabije stem?"
      // VAD voor PROACTIEF SPREKEN: tijdstip van de laatste aanhoudende spraak,
      // los van de gespreksmodus (de proactieve lus mag de mic hier enkel voor
      // openen). SPAN._recentSpeech() -> "praat er net iemand?" (laatste ~4s).
      const VAD_FRAMES = 6;     // ~100 ms aanhoudend boven de spraakdrempel
      let vadFrames = 0;
      SPAN._lastSpeechAt = 0;
      // spraakdrempel = duidelijk bóven de ruisvloer (relatief, niet vast) zodat
      // achtergrondgepraat de mic niet activeert/onderbreekt
      SPAN._speechThr = () => Math.max(0.06, noiseFloor * 2.2 + 0.03);
      const loop = () => {
        analyser.getByteFrequencyData(micData);
        let sum = 0;
        for (let i = 2; i < 40; i++) sum += micData[i];
        const raw = Math.min(1, sum / (38 * 160));
        const speaking = SPAN.state === "speaking";
        const active = mode !== "off" && !speaking;
        SPAN.micLevel += ((active ? raw : 0) - SPAN.micLevel) * 0.25; // smoothing
        // ruisvloer alleen bijwerken als het rustig is en de agent niet praat,
        // zodat 'ie de achtergrond volgt — niet jouw stem of de eigen TTS
        if (!speaking && raw < noiseFloor * 1.6 + 0.05) {
          noiseFloor += (raw - noiseFloor) * 0.05;
          noiseFloor = Math.min(0.35, Math.max(0.005, noiseFloor));
        }
        SPAN._recentPeak = Math.max(raw, SPAN._recentPeak * 0.92);
        // VAD-vlag: aanhoudende, nabije spraak (niet LO's eigen TTS) markeert het
        // moment; SPAN._recentSpeech() blijft daarna ~4s waar. Los van 'mode',
        // zodat de proactieve lus ook werkt met de mic enkel voor dit doel open.
        if (!speaking && raw > SPAN._speechThr()) {
          if (++vadFrames >= VAD_FRAMES) SPAN._lastSpeechAt = performance.now();
        } else if (vadFrames > 0) { vadFrames--; }
        // niveau van LO's eigen stem (echo-bewuste barge-in): tilt de drempel
        // mee omhoog terwijl LO praat, en laat 'm zakken in de stiltes
        if (ttsAnalyser && ttsLevelData) {
          ttsAnalyser.getByteFrequencyData(ttsLevelData);
          let ts = 0;
          for (let k = 2; k < 40; k++) ts += ttsLevelData[k];
          SPAN._ttsLevel += (Math.min(1, ts / (38 * 160)) - SPAN._ttsLevel) * 0.3;
        } else { SPAN._ttsLevel = 0; }
        // barge-in: duidelijk boven de ruisvloer/echo én een absolute bodem
        const bargeThr = Math.max(0.16, SPAN._speechThr() * 1.7) + SPAN._ttsLevel * 0.7;
        if (mode !== "off" && (speaking || SPAN.busy)) {
          if (raw > bargeThr) {
            if (++bargeFrames >= BARGE_FRAMES && !barged) { barged = true; onBargeIn(); }
          } else if (bargeFrames > 0) { bargeFrames--; }
        } else { bargeFrames = 0; barged = false; }  // beurt klaar -> reset
        requestAnimationFrame(loop);
      };
      SPAN._micSensing = true;   // mic-stream + analyser draaien nu (VAD bruikbaar)
      loop();
    } catch (e) { /* geen mic-permissie: HUD blijft werken */ }
  }
  // PROACTIEF SPREKEN mag de mic enkel voor VAD openen; hergebruikt de bestaande
  // stream als wake/gespreksmodus die al open heeft (initMicLevel is idempotent).
  SPAN.ensureMicSensing = async () => { await initMicLevel(); return !!SPAN._micSensing; };
  // "praat er net iemand?" — alleen betrouwbaar als de mic echt luistert
  SPAN._recentSpeech = () => !!SPAN._micSensing
    && (performance.now() - (SPAN._lastSpeechAt || 0) < 4000);

  /* -- waveform-canvas (#57/#58/#60/#62): oscilloscoop bij luisteren,
        gesimuleerde golf bij spreken, platte lijn met ruis bij rust ---------- */
  const wave = document.getElementById("wave");
  if (wave) {
    const wc = wave.getContext("2d");
    let simPhase = 0, lastW = 0, lastH = 0;
    function drawWave() {
      // width/height alleen toewijzen bij échte resize: toewijzen reset
      // de canvas-state en forceert een herallocatie, elke frame is zonde
      const wantW = wave.clientWidth * devicePixelRatio;
      const wantH = 56 * devicePixelRatio;
      if (wantW !== lastW || wantH !== lastH) {
        wave.width = lastW = wantW;
        wave.height = lastH = wantH;
      }
      const W = lastW, H = lastH;
      const FXon = !window.SPANFX || window.SPANFX.on(1);
      wc.clearRect(0, 0, W, H);
      if (!FXon) { requestAnimationFrame(drawWave); return; }
      const mid = H / 2;
      wc.lineWidth = 1.6 * devicePixelRatio;
      wc.strokeStyle = SPAN.state === "speaking" ? "rgba(190,240,255,.85)" : "rgba(56,225,255,.8)";
      wc.beginPath();

      if (SPAN.state === "listening" && analyser) {
        // MDN-recept: time-domein, normaliseren rond 128
        analyser.getByteTimeDomainData(timeData);
        const step = Math.ceil(timeData.length / (W / (2 * devicePixelRatio)));
        for (let x = 0, i = 0; i < timeData.length; i += step, x += 2 * devicePixelRatio) {
          const v = (timeData[i] - 128) / 128;
          const y = mid + v * mid * .9;
          x === 0 ? wc.moveTo(x, y) : wc.lineTo(x, y);
        }
      } else if (SPAN.state === "speaking") {
        // Siri-achtige gesimuleerde golf tijdens TTS (geen audio-stream beschikbaar)
        simPhase += .18;
        const amp = (Math.sin(simPhase * .7) * .3 + .55) * mid * .8;
        for (let x = 0; x <= W; x += 3 * devicePixelRatio) {
          const t = x / W;
          const env = Math.sin(Math.PI * t);  // randen vlak
          const y = mid + Math.sin(t * 18 + simPhase) * amp * env
            + Math.sin(t * 7 - simPhase * 1.4) * amp * .4 * env;
          x === 0 ? wc.moveTo(x, y) : wc.lineTo(x, y);
        }
      } else {
        // rust: vlakke lijn met heel lichte ruis
        for (let x = 0; x <= W; x += 6 * devicePixelRatio) {
          const y = mid + (Math.random() - .5) * 2 * devicePixelRatio;
          x === 0 ? wc.moveTo(x, y) : wc.lineTo(x, y);
        }
        wc.strokeStyle = "rgba(56,225,255,.25)";
      }
      wc.stroke();
      requestAnimationFrame(drawWave);
    }
    requestAnimationFrame(drawWave);
  }

  /* -- spraakherkenning: ÉÉN recognizer, drie modi -----------------------------
     off  = stil
     open = doorlopende 1:1 gespreksmodus (🎙): alles wat je zegt is input
     wake = passief luisteren (◉): activeren met "Jarvis", daarna 8s hot window
     Auto-herstart bij onend; fouten worden benoemd i.p.v. stil genegeerd. */
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const input = document.getElementById("input");
  const PLACEHOLDER = input ? input.placeholder : "";
  let mode = "off", recognizer = null, restartTimer = null;
  let intentionalStop = false, hotUntil = 0;

  if (!SR) {
    // iOS/Safari: geen (webkit)SpeechRecognition. Spraak blijft werken via
    // het bestaande server-STT-pad (MediaRecorder -> /api/stt); alleen als
    // óók opnemen onmogelijk is (geen MediaRecorder, of http zonder secure
    // context -> geen mediaDevices) verdwijnen de knoppen echt.
    if (!window.MediaRecorder || !navigator.mediaDevices) {
      $("mic").style.display = "none"; $("wake").style.display = "none";
      return;
    }
    localStorage.setItem("span_stt", "server");  // r689 pikt dit op
  }

  /* -- Fase 3: adaptieve beurt-aggregatie --------------------------------------
     Houd een afgeronde transcriptie kort vast en stuur 'm pas als je echt klaar
     lijkt. Klinkt de zin af (eindigt op .?! of gewoon netjes) -> kort venster;
     lijkt 'ie onaf (eindigt op een voegwoord/filler, of erg kort) -> langer
     venster, zodat een denkpauze midden in je zin je niet afkapt. Komt er binnen
     het venster meer spraak, dan smelt die in dezelfde beurt. */
  const CONT_CUES = ["en", "maar", "want", "dus", "of", "omdat", "zodat", "eh",
    "ehm", "uh", "uhm", "nog", "ook", "plus", "dat", "die", "met", "voor", "naar"];
  let turnBuf = "", turnTimer = null;
  function looksUnfinished(t) {
    const trimmed = t.trim();
    if (/[.?!]$/.test(trimmed)) return false;                  // duidelijke afsluiting
    const words = trimmed.toLowerCase().replace(/[.,!?]/g, "").split(/\s+/);
    if (words.length <= 2) return true;                        // erg kort -> wellicht niet af
    if (CONT_CUES.includes(words[words.length - 1])) return true; // eindigt op voegwoord/filler
    return false;
  }
  function flushTurn() {
    clearTimeout(turnTimer); turnTimer = null;
    const t = turnBuf.trim(); turnBuf = "";
    if (input) input.placeholder = PLACEHOLDER;
    if (t) SPAN.send(t);
  }
  function aggregate(text) {
    turnBuf = (turnBuf ? turnBuf + " " : "") + text.trim();
    if (input) input.placeholder = "… " + turnBuf;             // zichtbaar dat ik nog luister
    clearTimeout(turnTimer);
    turnTimer = setTimeout(flushTurn, looksUnfinished(turnBuf) ? 1500 : 600);
  }

  function handleFinal(text) {
    text = text.trim();
    if (!text) return;
    if (SPAN.state === "speaking") {                       // barge-in of eigen echo
      if (isStop(text)) stopSpeaking();
      return;
    }
    if (similarity(text, lastTTS.slice(0, text.length + 30)) >= 0.7) return; // echo
    // achtergrond-filter: alleen reageren als er net duidelijke (nabije) stem
    // was. Stil/ver achtergrondgepraat dat de browser tóch transcribeert blijft
    // onder de ruisdrempel en wordt genegeerd. ALLEEN voor browser-spraak: die
    // levert direct een final. Bij server-Whisper draait handleFinal pas ~1s ná
    // de spraak (transcriptie-roundtrip) -> recentPeak is dan al weggezakt, dus
    // daar zou de gate de zin onterecht droppen. Server-STT gate't toch al op
    // energie bij de opname-START. (Wake-modus regelt dit via het wake-woord.)
    if (mode === "open" && !serverSTT && SPAN._recentPeak !== undefined
        && SPAN._speechThr && SPAN._recentPeak < SPAN._speechThr()) {
      return;
    }
    if (mode === "open") {
      // beurt loopt nog (we onderbraken net): bewaar de zin en stuur 'm zodra de
      // afgebroken beurt is afgerond — zo gaat je aanvulling niet verloren.
      if (SPAN.busy) { SPAN._pendingText = text; return; }
      aggregate(text);                                    // Fase 3: even wachten op aanvulling
      return;
    }
    // wake-modus
    const command = findWake(text);
    if (command !== null) {
      SPAN.chime(880, .1);
      if (SPAN.acknowledge) SPAN.acknowledge();
      if (command.length > 2) { SPAN.send(command); return; }
      hotUntil = Date.now() + 8000;                        // alleen "Jarvis" → ik luister
      SPAN.sys("· ik luister ·");
      return;
    }
    if (Date.now() < hotUntil && !SPAN.busy) { aggregate(text); return; }
    // niets getriggerd: laat zien wát er gehoord is, zodat stilte niet als
    // 'kapot' voelt — en je ziet of het wake-woord verkeerd verstaan werd
    if (text.length > 1) SPAN.sys('gehoord: "' + text + '" · zeg "' + (SPAN._agentName || "LO") + '" om mij te wekken');
  }

  function buildRecognizer() {
    const r = new SR();
    r.lang = "nl-NL"; r.continuous = true; r.interimResults = true;
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) handleFinal(t);
        else interim += t;
      }
      if (interim && mode === "open" && input) input.placeholder = "… " + interim.trim();
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return; // normaal verloop
      if (e.error === "network" || e.error === "service-not-allowed") {
        switchToServerSTT();  // browser-spraakdienst geblokkeerd → eigen Whisper
        return;
      }
      const uitleg = {
        "not-allowed": "microfoon geweigerd — klik op het slotje in de adresbalk en sta de microfoon toe",
        "audio-capture": "geen microfoon gevonden",
      }[e.error] || e.error;
      SPAN.sys("Spraakherkenning: " + uitleg, "warn");
      if (e.error === "not-allowed") setVoiceMode("off");
    };
    r.onend = () => {
      if (input) input.placeholder = PLACEHOLDER;
      if (mode !== "off" && !intentionalStop) {            // browser kapt af → herstart
        clearTimeout(restartTimer);
        restartTimer = setTimeout(() => { try { r.start(); } catch (err) {} }, 400);
      }
      intentionalStop = false;
    };
    return r;
  }

  function setVoiceMode(next) {
    mode = next;
    $("mic").classList.toggle("active", mode === "open");
    $("wake").classList.toggle("active", mode === "wake");
    if (mode === "off") {
      intentionalStop = true; clearTimeout(restartTimer);
      clearTimeout(turnTimer); turnTimer = null; turnBuf = "";  // aggregatie stoppen
      try { recognizer && recognizer.stop(); } catch (e) {}
      stopSegment(true);
      if (SPAN.state === "listening") SPAN.setState("idle");
      return;
    }
    initMicLevel();
    if (serverSTT) { ensureSegmentLoop(); return; }
    recognizer = recognizer || buildRecognizer();
    try { recognizer.start(); } catch (e) { /* draait al */ }
  }

  /* -- server-STT: Whisper in de container (proxy-proof) ----------------------
     Volume-gestuurde segmenten: opname start zodra er stem is, stopt na
     ~1.2s stilte, segment gaat naar /api/stt en het resultaat volgt dezelfde
     route als browser-spraak (handleFinal). */
  let serverSTT = localStorage.getItem("span_stt") === "server";
  let recorder = null, segChunks = [], segStart = 0, silenceSince = 0, segTimer = null;

  function switchToServerSTT() {
    if (serverSTT) return;
    serverSTT = true;
    localStorage.setItem("span_stt", "server");
    intentionalStop = true;
    try { recognizer && recognizer.stop(); } catch (e) {}
    SPAN.sys("Browser-spraakdienst geblokkeerd (proxy) — overgeschakeld naar " +
      (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO") +
      "'s eigen spraakherkenning (Whisper op de server). Eerste zin duurt " +
      "even: het model wordt eenmalig geladen.");
    ensureSegmentLoop();
  }

  function ensureSegmentLoop() {
    if (segTimer) return;
    segTimer = setInterval(() => {
      if (mode === "off") { stopSegment(true); return; }
      if (!micStream) return;
      const level = SPAN.micLevel;
      // relatieve drempel: duidelijk boven de omgevingsruis (anti-achtergrond)
      const thr = SPAN._speechThr ? SPAN._speechThr() : 0.06;
      const talking = level > thr;
      if (!recorder && talking && SPAN.state !== "speaking" && (!SPAN.busy || barged)) {
        startSegment();
      } else if (recorder) {
        if (talking) silenceSince = 0;
        else if (!silenceSince) silenceSince = Date.now();
        else if (Date.now() - silenceSince > 1200) stopSegment(false);
        if (Date.now() - segStart > 25000) stopSegment(false); // harde limiet
      }
    }, 120);
  }

  function startSegment() {
    try {
      segChunks = [];
      recorder = new MediaRecorder(micStream,
        MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? { mimeType: "audio/webm;codecs=opus" } : undefined);
      recorder.ondataavailable = (e) => { if (e.data.size) segChunks.push(e.data); };
      recorder.onstop = onSegmentDone;
      recorder.start();
      segStart = Date.now(); silenceSince = 0;
      SPAN.setState("listening");
    } catch (e) { recorder = null; }
  }

  function stopSegment(discard) {
    if (discard) { clearInterval(segTimer); segTimer = null; }
    if (recorder && recorder.state !== "inactive") {
      recorder._discard = discard;
      recorder.stop();
    } else if (discard) {
      recorder = null;
    }
  }

  async function onSegmentDone() {
    const rec = recorder; recorder = null;
    const blob = new Blob(segChunks, { type: "audio/webm" });
    segChunks = [];
    if (rec && rec._discard) return;
    if (blob.size < 4000 || Date.now() - segStart < 600) return; // te kort = ruis
    if (input) input.placeholder = "… verwerken …";
    try {
      const res = await fetch("/api/stt", {
        method: "POST",
        headers: { ...SPAN.authHeaders(), "Content-Type": "application/octet-stream" },
        body: blob,
      });
      if (input) input.placeholder = PLACEHOLDER;
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        SPAN.sys("Server-spraakherkenning: " + (d.detail || res.status), "warn");
        return;
      }
      const d = await res.json();
      if (d.text) handleFinal(d.text);
    } catch (e) {
      if (input) input.placeholder = PLACEHOLDER;
    }
  }

  /* gespreksmodus toont continu 'luistert' zodra Span vrij is */
  setInterval(() => {
    if (mode === "open" && SPAN.state === "idle") SPAN.setState("listening");
  }, 700);

  $("mic").onclick = () => {
    if (mode === "open") { setVoiceMode("off"); SPAN.sys("Gespreksmodus uit."); return; }
    setVoiceMode("open");
    SPAN.sys("Gespreksmodus aan — doorlopend luisteren, gewoon praten. " +
      "'Stop' onderbreekt het voorlezen; 🎙 opnieuw klikken zet uit.");
  };
  $("wake").onclick = () => {
    if (mode === "wake") { setVoiceMode("off"); SPAN.sys("Wake word uit."); return; }
    setVoiceMode("wake");
    const w = (window.SPAN && SPAN._agentName ? SPAN._agentName : "LO");
    SPAN.sys("Wake word actief — zeg '" + w + "' + commando, of alleen '" + w + "' en praat daarna door.");
  };

  /* compat met de TTS-laag: na het uitspreken kort doorpraten zonder wake word */
  function openHotWindow() {
    if (mode === "wake") { hotUntil = Date.now() + 6000; }
  }
})();
