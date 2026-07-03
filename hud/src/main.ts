// NEBULA-HUD — N2: het echte brein (werkplan docs/werkplan-hud-nebula.md).
// De scene draait op /api/graph-data (LO-adapter), ververst elke 120s
// incrementeel (nieuwe memories landen met een lichtpuls), en is interactief:
// hover-tooltip + klik → detailpaneel + camera-vlucht. Zonder auth of bij een
// fout valt hij terug op de synthetische demo-data. N3 koppelt de live
// WS-signalen (leescascade + agent-status).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { fetchLoGraph, type LoGraph } from './data/lo';
import { setupInteractions, type Interactions } from './interactions';
import { NeuralActivity } from './scene/activity';
import { Highlighter, makeGlowTexture, MemoryPulses } from './scene/fx';
import { createKnowledgeGraph, type KnowledgeGraph } from './scene/graph';
import { createOrb, type OrbSettings } from './scene/orb';
import { createPostChain } from './scene/post';
import { AgentStateMock, type AgentState } from './state/agent';
import { MockMemoryStream } from './stream/mock';
import type { PositionedNode } from './data/synthetic';

export type LoState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface NebulaHandle {
  /** echte agent-status uit de LO-app (busy is daar al naar thinking gemapt) */
  setState(state: LoState): void;
  /** open acties in de Agent Inbox -> waarschuwingsmodus zodra de agent stil is */
  setAlert(on: boolean): void;
  /** live leescascade: LO raadpleegt deze memories (WS memory_read) */
  markReading(ids: string[], reason?: string): void;
  /** orb-tuning uit Instellingen -> Uiterlijk (dichtheid/flitsen/aders/ringen) */
  setSettings(s: OrbSettings): void;
  /** cinema-look aan/uit (scherptediepte/aberratie/korrel); uit = kraakhelder */
  setCinema(on: boolean): void;
  /** node-stijl: 'zacht' (gloeipunten) of 'strak' (harde kleinere kernen) */
  setNodeStyle(style: 'zacht' | 'strak'): void;
  unmount(): void;
}

export interface MountOptions {
  /** mobiel/zwakke GPU: 16k i.p.v. 65k deeltjes, minimale post-keten */
  lite?: boolean;
  /** auth-headers van de LO-app (SPAN.authHeaders) — zonder: synthetische demo */
  authHeaders?: () => Record<string, string>;
}

export function webgl2Available(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch {
    return false;
  }
}

export function detectLite(): boolean {
  const coarse = window.matchMedia?.('(pointer: coarse)').matches ?? false;
  return coarse || window.innerWidth < 900;
}

// NEBULA-layout (variant 1 uit de sandbox): brein als wolk rondom de agent
const CAMERA: [number, number, number] = [420, 180, 620];
const CAVITY = 215;
const AUTO_ROTATE = 0.35;
const REFRESH_MS = 120_000;

export function mount(container: HTMLElement, opts: MountOptions = {}): NebulaHandle {
  const lite = opts.lite ?? detectLite();

  const renderer = new THREE.WebGLRenderer({
    powerPreference: 'high-performance',
    antialias: false, // de post-keten (SMAA) doet het eindbeeld
    stencil: false,
  });
  renderer.setPixelRatio(lite ? 1 : Math.min(window.devicePixelRatio, 2));
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x030712, 0.00055);

  const camera = new THREE.PerspectiveCamera(55, 1, 1, 6000);
  camera.position.set(...CAMERA);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.autoRotate = true;
  controls.autoRotateSpeed = AUTO_ROTATE;
  controls.enableZoom = false; // voorpagina: geen scroll-jacking; N4 maakt dit instelbaar
  controls.minDistance = 180;
  controls.maxDistance = 2200;

  scene.add(new THREE.AmbientLight(0x334155, 2.2));
  const key = new THREE.DirectionalLight(0x7dd3fc, 1.6);
  key.position.set(1, 1, 0.5);
  scene.add(key);

  // --- vaste inhoud --------------------------------------------------------------
  const orb = createOrb(renderer, 110, lite ? 128 : 256);
  scene.add(orb.group);

  const glowTex = makeGlowTexture();
  const pulses = new MemoryPulses(glowTex);
  scene.add(pulses.group);
  const highlighter = new Highlighter(glowTex);
  scene.add(highlighter.group);

  const post = createPostChain(renderer, scene, camera, lite);
  const agent = new AgentStateMock();

  // N3: echte status + alert-overlay. Alert toont alleen als de agent stil is
  // (een lopend antwoord niet visueel onderbreken); de inbox-badge blijft toch.
  let lastState: LoState = 'idle';
  let alertOn = false;
  const applyState = (): void => {
    const effective: AgentState = alertOn && lastState === 'idle' ? 'alert' : lastState;
    agent.set(effective, true); // manual -> auto-demo stopt definitief
  };

  // reden-label bij de leescascade ("leest · waarom")
  const reason = document.createElement('div');
  reason.id = 'nebula-reason';
  container.appendChild(reason);
  let reasonTimer: ReturnType<typeof setTimeout> | null = null;
  const showReason = (text: string): void => {
    reason.textContent = text;
    reason.classList.add('open');
    if (reasonTimer) clearTimeout(reasonTimer);
    reasonTimer = setTimeout(() => reason.classList.remove('open'), 4500);
  };

  // --- camera-vluchten (selectie/terug) --------------------------------------------
  const camGoal = new THREE.Vector3(...CAMERA);
  const targetGoal = new THREE.Vector3();
  let transitioning = false;

  // --- brein: echte data, met synthetische demo als vangnet --------------------------
  let graph: KnowledgeGraph | null = null;
  let activity: NeuralActivity | null = null;
  let interactions: Interactions | null = null;
  let stream: MockMemoryStream | null = null;
  let refreshTimer: ReturnType<typeof setInterval> | null = null;
  const types = new Map<string, string>();
  const pendingPulses: { node: PositionedNode; age: number }[] = [];

  const attachGraph = (g: KnowledgeGraph): void => {
    graph = g;
    // N4 (keuze Bas): losse memories niet laten wegzweven — een mílde
    // schilkracht dikt de wolk in; verbonden clusters houden hun vorm
    g.setForces({ radial: 400, radialStrength: 0.15, y: null, yStrength: 0, cavity: CAVITY });
    scene.add(g.object);
    activity = new NeuralActivity(g, glowTex);
    scene.add(activity.group);
    interactions = setupInteractions({
      renderer,
      camera,
      graph: g,
      highlighter,
      typeOf: (id) => types.get(id) ?? '',
      authHeaders: opts.authHeaders,
      onFocusNode(pos) {
        const dir = camera.position.clone().sub(controls.target).normalize();
        camGoal.copy(pos).add(dir.multiplyScalar(240));
        targetGoal.copy(pos);
        transitioning = true;
      },
      onSelectionChange(node) {
        controls.autoRotate = node === null;
        if (node === null) {
          camGoal.set(...CAMERA);
          targetGoal.set(0, 0, 0);
          transitioning = true;
        }
      },
    });
  };

  const refresh = async (): Promise<void> => {
    if (document.hidden || !graph || !opts.authHeaders) return;
    try {
      const d = await fetchLoGraph(opts.authHeaders());
      const nieuwe = d.nodes.filter((n) => !graph!.getNode(n.id));
      if (nieuwe.length === 0) return;
      for (const [id, t] of d.types) types.set(id, t);
      const nieuweIds = new Set(nieuwe.map((n) => n.id));
      const nieuweLinks = d.links.filter(
        (l) => nieuweIds.has(l.source) || nieuweIds.has(l.target)
      );
      graph!.addMemories(nieuwe, nieuweLinks);
      for (const n of nieuwe) pendingPulses.push({ node: n, age: 0 });
    } catch {
      /* stil — volgende poging over 120s */
    }
  };

  const boot = async (): Promise<void> => {
    if (opts.authHeaders) {
      try {
        const d: LoGraph = await fetchLoGraph(opts.authHeaders());
        for (const [id, t] of d.types) types.set(id, t);
        attachGraph(createKnowledgeGraph({ nodes: d.nodes, links: d.links }));
        refreshTimer = setInterval(() => void refresh(), REFRESH_MS);
        return;
      } catch (e) {
        console.warn('[nebula] echte graafdata laden mislukt, demo-data:', e);
      }
    }
    // demo-modus: synthetisch brein + mock-stream (zoals de sandbox)
    attachGraph(createKnowledgeGraph());
    stream = new MockMemoryStream(() => graph!.nodeIds(), 100_000, 4000);
    stream.start();
  };
  void boot();

  // --- maat & zichtbaarheid -----------------------------------------------------
  const resize = (): void => {
    const w = Math.max(1, container.clientWidth);
    const h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false);
    post.composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // --- adaptieve kwaliteit (sandbox-logica) --------------------------------------
  let frames = 0;
  let windowStart = performance.now();
  let cosmetics = !lite;

  const adaptQuality = (now: number): void => {
    frames++;
    if (now - windowStart < 2000) return;
    const fps = (frames * 1000) / (now - windowStart);
    frames = 0;
    windowStart = now;
    if (fps < 40 && cosmetics) {
      cosmetics = false;
      post.setCosmetics(false);
      renderer.setPixelRatio(1);
    } else if (fps > 55 && !cosmetics && !lite) {
      cosmetics = true;
      post.setCosmetics(true);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    }
  };

  // --- render-loop -----------------------------------------------------------------
  const t0 = performance.now();
  let prev = t0;

  renderer.setAnimationLoop(() => {
    if (document.hidden) return; // verborgen tab: geen GPU/CPU (Fase D-lijn)
    const now = performance.now();
    const t = (now - t0) / 1000;
    const dt = now - prev;
    prev = now;

    agent.update(now);

    // demo-stream leegmaken aan het begin van het frame (nooit mid-render)
    if (stream && graph) {
      const events = stream.drain();
      if (events.length > 0) {
        graph.addMemories(
          events.map((e) => e.node),
          events.flatMap((e) => e.links)
        );
        for (const e of events) pendingPulses.push({ node: e.node, age: 0 });
      }
    }
    // "memory landt": puls pas als de simulatie de node een plek gaf
    for (let i = pendingPulses.length - 1; i >= 0; i--) {
      const p = pendingPulses[i]!;
      p.age += dt;
      const n = p.node;
      const r = Math.hypot(n.x ?? 0, n.y ?? 0, n.z ?? 0);
      if (r > 90) {
        pulses.spawn(n);
        pendingPulses.splice(i, 1);
      } else if (p.age > 5000) {
        pendingPulses.splice(i, 1);
      }
    }

    if (transitioning) {
      camera.position.lerp(camGoal, 0.045);
      controls.target.lerp(targetGoal, 0.045);
      if (camera.position.distanceTo(camGoal) < 2) transitioning = false;
    }

    graph?.tick();
    orb.update(t, dt, agent.state);
    pulses.update(dt);
    activity?.update(dt, agent.state);
    interactions?.update(t);
    controls.update();
    post.setFocus(controls.target);
    adaptQuality(now);
    post.composer.render();
  });

  const GELDIGE_STATES: LoState[] = ['idle', 'listening', 'thinking', 'speaking'];
  return {
    setState(state: LoState) {
      // grens-validatie: LO kent ook tussenstanden (bv. "boot") — alles wat
      // de orb niet kent wordt rust, anders crasht BEHAVIOUR[state] de loop
      lastState = GELDIGE_STATES.includes(state) ? state : 'idle';
      applyState();
    },
    setAlert(on: boolean) {
      alertOn = on;
      applyState();
    },
    setSettings(sett: OrbSettings) {
      orb.setSettings(sett);
    },
    setCinema(on: boolean) {
      post.setCinema(on);
    },
    setNodeStyle(style: 'zacht' | 'strak') {
      graph?.setNodeStyle(style === 'strak' ? 'strak' : 'zacht');
    },
    markReading(ids: string[], reasonText?: string) {
      if (!graph || !activity) return;
      const bekend = ids.filter((id) => graph!.getNode(id));
      if (bekend.length > 0) activity.activate(bekend);
      if (reasonText) showReason(`leest · ${reasonText}`);
      // onbekende ids = memories die net geschreven zijn -> wolk bijwerken
      if (bekend.length < ids.length && opts.authHeaders) void refresh();
    },
    unmount() {
      if (reasonTimer) clearTimeout(reasonTimer);
      reason.remove();
      renderer.setAnimationLoop(null);
      if (refreshTimer) clearInterval(refreshTimer);
      stream?.stop();
      interactions?.dispose();
      ro.disconnect();
      controls.dispose();
      post.composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
