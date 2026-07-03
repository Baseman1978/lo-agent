// NEBULA-HUD — N1: scene-port (werkplan docs/werkplan-hud-nebula.md).
// De volledige sandbox-scene (GPGPU-orb + geheugenwolk + cinema-post) draait
// als center-achtergrond van LO. Data is in deze fase nog synthetisch
// (mock-stream); N2 koppelt /api/graph, N3 de live WS-signalen.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { NeuralActivity } from './scene/activity';
import { makeGlowTexture, MemoryPulses } from './scene/fx';
import { createKnowledgeGraph } from './scene/graph';
import { createOrb } from './scene/orb';
import { createPostChain } from './scene/post';
import { AgentStateMock, type AgentState } from './state/agent';
import { MockMemoryStream } from './stream/mock';

export interface NebulaHandle {
  /** agent-status (N3 koppelt dit aan de echte SPAN-signalen) */
  setState(state: AgentState): void;
  unmount(): void;
}

export interface MountOptions {
  /** mobiel/zwakke GPU: 16k i.p.v. 65k deeltjes, minimale post-keten */
  lite?: boolean;
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
  controls.minDistance = 180;
  controls.maxDistance = 2200;

  scene.add(new THREE.AmbientLight(0x334155, 2.2));
  const key = new THREE.DirectionalLight(0x7dd3fc, 1.6);
  key.position.set(1, 1, 0.5);
  scene.add(key);

  // --- inhoud -----------------------------------------------------------------
  const orb = createOrb(renderer, 110, lite ? 128 : 256);
  scene.add(orb.group);

  const graph = createKnowledgeGraph();
  graph.setForces({ radial: null, y: null, yStrength: 0, cavity: CAVITY });
  scene.add(graph.object);

  const glowTex = makeGlowTexture();
  const pulses = new MemoryPulses(glowTex);
  scene.add(pulses.group);
  const activity = new NeuralActivity(graph, glowTex);
  scene.add(activity.group);

  const post = createPostChain(renderer, scene, camera, lite);

  // --- agent-status: auto-demo tot N3 de echte signalen koppelt ----------------
  const agent = new AgentStateMock();

  // --- mock-stream: nieuwe memories landen met een lichtpuls (drain-pattern) ---
  const stream = new MockMemoryStream(() => graph.nodeIds(), 100_000, 4000);
  stream.start();

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
  const pendingPulses: { node: import('./data/synthetic').PositionedNode; age: number }[] = [];

  renderer.setAnimationLoop(() => {
    if (document.hidden) return; // verborgen tab: geen GPU/CPU (Fase D-lijn)
    const now = performance.now();
    const t = (now - t0) / 1000;
    const dt = now - prev;
    prev = now;

    agent.update(now);

    // stream-buffer leegmaken aan het begin van het frame (nooit mid-render)
    const events = stream.drain();
    if (events.length > 0) {
      graph.addMemories(
        events.map((e) => e.node),
        events.flatMap((e) => e.links)
      );
      for (const e of events) pendingPulses.push({ node: e.node, age: 0 });
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

    graph.tick();
    orb.update(t, dt, agent.state);
    pulses.update(dt);
    activity.update(dt, agent.state);
    controls.update();
    post.setFocus(controls.target);
    adaptQuality(now);
    post.composer.render();
  });

  return {
    setState(state: AgentState) {
      agent.set(state, true);
    },
    unmount() {
      renderer.setAnimationLoop(null);
      stream.stop();
      ro.disconnect();
      controls.dispose();
      post.composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
