// LO-adapter: /api/graph → NEBULA-graafdata. Kleuren/groottes volgen de
// kleurtaal van het bestaande hologram (cyaan = denkweefsel, warm = aandacht).

import type { GraphData, MemoryLink, MemoryNode } from './synthetic';

export const LO_COLORS: Record<string, string> = {
  Identity: '#ffffff',
  MemoryFragment: '#00d4ff',
  Insight: '#7dffb4',
  Mistake: '#ff6b6b',
  Idea: '#ffd27d',
  Quest: '#ff9d5c',
  QuestStep: '#9a6b3f',
  Skill: '#a06bff',
  Protocol: '#38e1ff',
  Session: '#274a63',
  Entity: '#ff8ad8',
  Meeting: '#5cd6c0',
  Document: '#9fcf86',
};
const FALLBACK_COLOR = '#7a96aa';

// basisgrootte per type (bovenop de verbindingsgraad — hubs worden vanzelf groter)
const BASE_VAL: Record<string, number> = {
  Identity: 7, Protocol: 3.2, Quest: 3.2, Skill: 3.2, Insight: 2.6,
  Entity: 2.2, Meeting: 2.2, Document: 2.2,
};

interface ApiNode { id: string; type: string; key: string; label: string }
interface ApiLink { source: string; target: string; rel?: string }

export interface LoGraph extends GraphData {
  /** LO-type per node-id (voor het detailpaneel) */
  types: Map<string, string>;
}

export async function fetchLoGraph(
  headers: Record<string, string>,
  limit = 450
): Promise<LoGraph> {
  const res = await fetch(`/api/graph?limit=${limit}`, { headers });
  if (!res.ok) throw new Error(`graph ${res.status}`);
  const d = (await res.json()) as { nodes: ApiNode[]; links: ApiLink[] };

  const degree = new Map<string, number>();
  for (const l of d.links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1);
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1);
  }

  const types = new Map<string, string>();
  const nodes: MemoryNode[] = d.nodes.map((n) => {
    types.set(n.id, n.type);
    return {
      id: n.id,
      dataClass: n.type,
      color: LO_COLORS[n.type] ?? FALLBACK_COLOR,
      val: (BASE_VAL[n.type] ?? 1) + Math.min(6, (degree.get(n.id) ?? 0) * 0.45),
      label: (n.label || n.type).slice(0, 70),
      createdAt: '',
    };
  });
  const ids = new Set(nodes.map((n) => n.id));
  const links: MemoryLink[] = d.links
    .filter((l) => ids.has(l.source) && ids.has(l.target))
    .map((l) => ({ source: l.source, target: l.target }));
  return { nodes, links, types };
}
