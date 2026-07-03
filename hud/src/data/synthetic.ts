// Synthetic data only — this sandbox never touches real databases (Gate A/B closed).
// Data classes and colours follow baseline diagram D03; `work` uses the cloud-blue accent.

export const DATA_CLASSES = ['personal', 'knowledge', 'work', 'private_sensitive'] as const;
export type DataClass = (typeof DATA_CLASSES)[number];

export const CLASS_COLOR: Record<DataClass, number> = {
  personal: 0xfde047,
  knowledge: 0x86efac,
  work: 0x93c5fd,
  private_sensitive: 0xfdba74,
};

// Class mix roughly matching a personal knowledge base.
const CLASS_WEIGHTS: [DataClass, number][] = [
  ['knowledge', 0.45],
  ['personal', 0.3],
  ['work', 0.18],
  ['private_sensitive', 0.07],
];

export interface MemoryNode {
  id: string;
  dataClass: string;  // synthetisch: DataClass; LO: het Neo4j-label (Insight, ...)
  color: string;
  val: number; // node size weight
  label: string;
  createdAt: string; // ISO date (synthetic)
}

/** node zoals de d3-simulatie hem verrijkt (posities verschijnen na layout) */
export type PositionedNode = MemoryNode & { x?: number; y?: number; z?: number };

const VOCAB: Record<DataClass, string[][]> = {
  personal: [
    ['notitie', 'idee', 'herinnering', 'voorkeur', 'plan'],
    ['vakantie', 'hardlopen', 'boekenlijst', 'verjaardag', 'recept', 'muziek'],
  ],
  knowledge: [
    ['artikel', 'paper', 'handleiding', 'snippet', 'referentie'],
    ['pgvector', 'Three.js', 'Tailscale', 'restic', 'GDPR', 'Fastify'],
  ],
  work: [
    ['verslag', 'actiepunt', 'concept', 'planning', 'notulen'],
    ['sprint', 'review', 'roadmap', 'overleg', 'audit'],
  ],
  private_sensitive: [
    ['document', 'dossier', 'afspraak', 'formulier'],
    ['[geredacteerd]'],
  ],
};

function makeLabel(cls: DataClass, rand: () => number): string {
  const [kinds, topics] = VOCAB[cls];
  const kind = kinds![Math.floor(rand() * kinds!.length)]!;
  const topic = topics![Math.floor(rand() * topics!.length)]!;
  return `${kind} · ${topic}`;
}

function makeDate(rand: () => number): string {
  // synthetic timestamps between 2024-01 and 2026-07
  const start = Date.UTC(2024, 0, 1);
  const end = Date.UTC(2026, 6, 1);
  return new Date(start + rand() * (end - start)).toISOString().slice(0, 10);
}

export interface MemoryLink {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: MemoryNode[];
  links: MemoryLink[];
}

// Deterministic PRNG (mulberry32) so every run shows the same synthetic brain.
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function pickClass(rand: () => number): DataClass {
  const r = rand();
  let acc = 0;
  for (const [cls, w] of CLASS_WEIGHTS) {
    acc += w;
    if (r < acc) return cls;
  }
  return 'knowledge';
}

export function makeNode(i: number, rand: () => number): MemoryNode {
  const dataClass = pickClass(rand);
  return {
    id: `mem-${i}`,
    dataClass,
    color: '#' + CLASS_COLOR[dataClass].toString(16).padStart(6, '0'),
    val: 0.5 + rand() * (rand() < 0.06 ? 9 : 2.2), // few hubs, many small memories
    label: makeLabel(dataClass, rand),
    createdAt: makeDate(rand),
  };
}

/** ~500 nodes / ~1500 edges with preferential attachment (hub-y, brain-like). */
export function generateGraph(nodeCount = 500, avgDegree = 3): GraphData {
  const rand = rng(20260702);
  const nodes: MemoryNode[] = [];
  const links: MemoryLink[] = [];

  for (let i = 0; i < nodeCount; i++) {
    const node = makeNode(i, rand);
    nodes.push(node);
    if (i === 0) continue;
    const edges = 1 + Math.floor(rand() * avgDegree);
    for (let e = 0; e < edges && links.length < nodeCount * avgDegree; e++) {
      // preferential attachment: bias towards low indices (older, better-connected memories)
      const t = Math.floor(Math.pow(rand(), 2.2) * i);
      const target = nodes[t];
      if (target && target.id !== node.id) links.push({ source: node.id, target: target.id });
    }
  }
  return { nodes, links };
}

/**
 * ~50k synthetic "embedding" points: four gaussian class-clusters on a shell
 * around the brain — stands in for offline-precomputed PCA/PaCMAP coordinates.
 * Returns interleaved positions (xyz) and colors (rgb) ready for BufferGeometry.
 */
export function generatePointCloud(count = 50_000, shellRadius = 620): {
  positions: Float32Array;
  colors: Float32Array;
} {
  const rand = rng(42);
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);

  // cluster centres: four directions on the shell
  const centers = DATA_CLASSES.map((_, i) => {
    const phi = (i / DATA_CLASSES.length) * Math.PI * 2;
    const y = (i % 2 === 0 ? 0.45 : -0.45) * shellRadius;
    const r = Math.sqrt(shellRadius * shellRadius - y * y);
    return [Math.cos(phi) * r, y, Math.sin(phi) * r] as const;
  });

  const gauss = () => (rand() + rand() + rand() + rand() - 2) / 2; // approx N(0, 0.5)

  for (let i = 0; i < count; i++) {
    const cls = pickClass(rand);
    const ci = DATA_CLASSES.indexOf(cls);
    const c = centers[ci]!;
    const spread = shellRadius * 0.38;
    positions[i * 3] = c[0] + gauss() * spread;
    positions[i * 3 + 1] = c[1] + gauss() * spread;
    positions[i * 3 + 2] = c[2] + gauss() * spread;

    const col = CLASS_COLOR[cls];
    const dim = 0.35 + rand() * 0.5; // vary brightness so bloom picks out a subset
    colors[i * 3] = (((col >> 16) & 0xff) / 255) * dim;
    colors[i * 3 + 1] = (((col >> 8) & 0xff) / 255) * dim;
    colors[i * 3 + 2] = ((col & 0xff) / 255) * dim;
  }
  return { positions, colors };
}
