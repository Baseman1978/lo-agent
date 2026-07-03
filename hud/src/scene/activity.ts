import * as THREE from 'three';
import type { PositionedNode } from '../data/synthetic';
import type { AgentState } from '../state/agent';
import type { KnowledgeGraph } from './graph';

// Neurale activiteit in twee lagen:
//  1) basisactiviteit — continu een lage stroom impulsen over willekeurige verbindingen
//  2) denkgolf (THINKING) — spreading activation: vanaf een hub golft activiteit
//     hop-voor-hop door de buurverbindingen; geraakte nodes lichten op en doven uit.
// In productie wordt laag 2 gevoed door echte RAG-retrieval-events (welke memory-id's
// de agent raadpleegt) via activate(ids) — de mock kiest nu zelf een hub.

const BASE_COLOR = '#38e5f8';
const WAVE_COLOR = '#efe4ff';
const BASE_SPARKS_PER_SEC = 9;
const HOP_INTERVAL_MS = 400;
const MAX_HOPS = 6; // diepere golf: gedachte trekt verder het brein in
const MAX_PER_HOP = 18;
const GLOW_LIFE_MS = 3400;
const WAVE_SPARK_WIDTH = 5;
const BURST = [0, 90, 185]; // salvo van drie impulsen per verbinding

interface Glow {
  sprite: THREE.Sprite;
  node: PositionedNode;
  age: number;
}

interface Wave {
  frontier: PositionedNode[];
  visited: Set<string>;
  hop: number;
  timer: number;
}

export class NeuralActivity {
  readonly group = new THREE.Group();
  private baseAcc = 0;
  private wave: Wave | null = null;
  private waveCooldown = 0;
  private glows: Glow[] = [];
  private pendingGlows: { node: PositionedNode; delay: number }[] = [];
  private pendingSparks: { a: string; b: string; delay: number }[] = [];
  private wasThinking = false;

  constructor(
    private graph: KnowledgeGraph,
    private tex: THREE.Texture
  ) {}

  /** productie-API: licht precies deze memories op (RAG-retrieval); mock gebruikt startWaveFromHub */
  activate(ids: string[]): void {
    const nodes = ids.map((id) => this.graph.getNode(id)).filter((n): n is PositionedNode => !!n);
    const seed = nodes[0];
    if (!seed) return;
    this.wave = { frontier: [seed], visited: new Set(ids), hop: 0, timer: HOP_INTERVAL_MS };
    for (const n of nodes) this.glow(n);
  }

  private startWaveFromHub(): void {
    // kies een hub (veel verbindingen) als "gedachte"-startpunt
    let best: PositionedNode | null = null;
    let bestDeg = 0;
    for (let i = 0; i < 30; i++) {
      const ids = this.graph.nodeIds();
      const id = ids[Math.floor(Math.random() * ids.length)];
      if (!id) continue;
      const deg = this.graph.neighbors(id).length;
      if (deg > bestDeg) {
        bestDeg = deg;
        best = this.graph.getNode(id) ?? null;
      }
    }
    if (!best) return;
    this.wave = { frontier: [best], visited: new Set([best.id]), hop: 0, timer: HOP_INTERVAL_MS };
    this.glow(best, 1.4);
  }

  private glow(node: PositionedNode, size = 1): void {
    const mat = new THREE.SpriteMaterial({
      map: this.tex,
      color: 0xc4b5fd,
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.setScalar((22 + node.val * 4) * size);
    this.group.add(sprite);
    this.glows.push({ sprite, node, age: 0 });
  }

  private stepWave(): void {
    const w = this.wave;
    if (!w) return;
    const next: PositionedNode[] = [];
    for (const node of w.frontier) {
      for (const nb of this.graph.neighbors(node.id)) {
        if (w.visited.has(nb.id) || next.length >= MAX_PER_HOP) continue;
        w.visited.add(nb.id);
        // salvo van drie dikke impulsen per verbinding — leest als één duidelijke ontlading
        for (const delay of BURST) this.pendingSparks.push({ a: node.id, b: nb.id, delay });
        // glow pas als de eerste impuls "aankomt"
        this.pendingGlows.push({ node: nb, delay: 280 });
        next.push(nb);
      }
    }
    w.frontier = next;
    w.hop++;
    if (w.hop >= MAX_HOPS || next.length === 0) {
      this.wave = null;
      this.waveCooldown = 2200 + Math.random() * 1800;
    }
  }

  update(dtMs: number, state: AgentState): void {
    if (!this.graph.ready()) return;

    // 1 · basisactiviteit — iets levendiger bij praten/luisteren, gedimd tijdens
    // denken zodat de golf duidelijk afsteekt tegen de achtergrond
    const rate =
      state === 'thinking'
        ? BASE_SPARKS_PER_SEC * 0.35
        : state === 'idle'
          ? BASE_SPARKS_PER_SEC
          : BASE_SPARKS_PER_SEC * 1.6;
    this.baseAcc += (dtMs / 1000) * rate;
    while (this.baseAcc >= 1) {
      this.baseAcc -= 1;
      this.graph.sparkRandom(BASE_COLOR);
    }

    // 2 · denkgolf
    const thinking = state === 'thinking';
    if (thinking && !this.wasThinking) this.startWaveFromHub();
    this.wasThinking = thinking;

    if (this.wave) {
      this.wave.timer += dtMs;
      if (this.wave.timer >= HOP_INTERVAL_MS) {
        this.wave.timer = 0;
        this.stepWave();
      }
    } else if (thinking) {
      this.waveCooldown -= dtMs;
      if (this.waveCooldown <= 0) this.startWaveFromHub();
    }

    // uitgestelde salvo-impulsen
    for (let i = this.pendingSparks.length - 1; i >= 0; i--) {
      const s = this.pendingSparks[i]!;
      s.delay -= dtMs;
      if (s.delay <= 0) {
        this.graph.spark(s.a, s.b, WAVE_COLOR, WAVE_SPARK_WIDTH);
        this.pendingSparks.splice(i, 1);
      }
    }

    // uitgestelde glows (impuls-reistijd)
    for (let i = this.pendingGlows.length - 1; i >= 0; i--) {
      const p = this.pendingGlows[i]!;
      p.delay -= dtMs;
      if (p.delay <= 0) {
        this.glow(p.node);
        this.pendingGlows.splice(i, 1);
      }
    }

    // glow-envelope: snel op, langzaam uit; volgt de (licht bewegende) node
    for (let i = this.glows.length - 1; i >= 0; i--) {
      const g = this.glows[i]!;
      g.age += dtMs;
      const p = g.age / GLOW_LIFE_MS;
      if (p >= 1) {
        this.group.remove(g.sprite);
        g.sprite.material.dispose();
        this.glows.splice(i, 1);
        continue;
      }
      g.sprite.position.set(g.node.x ?? 0, g.node.y ?? 0, g.node.z ?? 0);
      g.sprite.material.opacity = p < 0.15 ? p / 0.15 : 1 - (p - 0.15) / 0.85;
    }
  }
}
