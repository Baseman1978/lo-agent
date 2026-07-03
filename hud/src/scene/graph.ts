import { forceRadial, forceY } from 'd3-force-3d';
import * as THREE from 'three';
import ThreeForceGraph from 'three-forcegraph';
import { makeCoreTexture, makeGlowTexture } from './fx';
import {
  generateGraph,
  type GraphData,
  type MemoryLink,
  type MemoryNode,
  type PositionedNode,
} from '../data/synthetic';

export interface KnowledgeGraph {
  object: ThreeForceGraph;
  /** current node ids (for the mock stream to link against) */
  nodeIds(): string[];
  /** incremental update: keeps existing layout, reheats simulation only for new items */
  addMemories(nodes: MemoryNode[], links: MemoryLink[]): void;
  nodeCount(): number;
  tick(): void;
  /** reshape the layout (variant switch): radial shell, Y-plane pull en holte rond de orb */
  setForces(cfg: { radial: number | null; radialStrength?: number; y: number | null; yStrength: number; cavity: number }): void;
  getNode(id: string): PositionedNode | undefined;
  neighbors(id: string): PositionedNode[];
  /** true zodra de eerste tickFrame is geweest (particles/krachten zijn dan veilig) */
  ready(): boolean;
  /** vuur één impuls-deeltje af over de verbinding a→b; false als de link niet bestaat */
  spark(a: string, b: string, color: string, width?: number): boolean;
  /** vuur één impuls af over een willekeurige verbinding (basisactiviteit) */
  sparkRandom(color: string): void;
  /** node-stijl: 'zacht' (gloeipunten) of 'strak' (kleinere, hardere kernen) */
  setNodeStyle(style: 'zacht' | 'strak'): void;
}

/**
 * Knowledge graph as a THREE.Object3D (three-forcegraph, the engine underneath
 * vasturiano's 3d-force-graph) so it composes with our own renderer, brain,
 * point cloud and pmndrs postprocessing chain in a single scene.
 */
export function createKnowledgeGraph(initial?: GraphData): KnowledgeGraph {
  const data: GraphData = initial ?? generateGraph();

  // id → node en adjacency, bijgehouden bij elke toevoeging (links zijn hier
  // nog string-referenties; de simulatie muteert ze later naar object-refs)
  const byId = new Map<string, PositionedNode>(data.nodes.map((n) => [n.id, n]));
  const adjacency = new Map<string, Set<string>>();
  const addEdge = (a: string, b: string): void => {
    (adjacency.get(a) ?? adjacency.set(a, new Set()).get(a)!).add(b);
    (adjacency.get(b) ?? adjacency.set(b, new Set()).get(b)!).add(a);
  };
  for (const l of data.links) addEdge(l.source, l.target);

  // stap A "fijnheid": nodes als zachte gloeipunten i.p.v. 3D-bolletjes —
  // fijner beeld én ~10× goedkoper te renderen; hubs blijven groter via val.
  // 'strak' (smaakknop Bas): compacte harde kern met dunne gloedrand.
  const glowTex = makeGlowTexture();
  const coreTex = makeCoreTexture();
  let nodeStyle: 'zacht' | 'strak' = 'zacht';
  const nodeSprite = (n: any): THREE.Sprite => {
    const node = n as MemoryNode;
    const strak = nodeStyle === 'strak';
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: strak ? coreTex : glowTex,
        color: node.color,
        transparent: true,
        opacity: strak ? 1 : 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    sprite.scale.setScalar(strak ? 4 + node.val * 1.5 : 7 + node.val * 2.4);
    return sprite;
  };
  const graph = new ThreeForceGraph()
    .graphData(data)
    .nodeThreeObject(nodeSprite)
    .linkColor(() => '#38bdf8')
    .linkOpacity(0.1)
    // impulsen: geen permanente deeltjes — we vuren ze per stuk af via emitParticle
    .linkDirectionalParticles(0)
    .linkDirectionalParticleWidth((l: any) => l.__sparkWidth ?? 3.2)
    .linkDirectionalParticleSpeed(0.03)
    .linkDirectionalParticleResolution(4)
    .linkDirectionalParticleColor((l: any) => l.__sparkColor ?? '#22d3ee')
    // licht gebogen verbindingen: lange links buigen om de holte heen i.p.v.
    // dwars door het midden, en het oogt organischer
    .linkCurvature(0.25)
    .linkCurveRotation((l: any) => ((l.__curveSeed ??= Math.random()) * Math.PI * 2))
    .warmupTicks(80)
    .cooldownTicks(200);

  // The internal d3 layout only exists after the first tickFrame() digest —
  // applying forces (and reheating) before that crashes the engine. Queue
  // force changes requested before the first tick and apply them right after it.
  let ticked = false;
  let pendingForces: Parameters<KnowledgeGraph['setForces']>[0] | null = null;

  // holte rond de orb: nodes worden elke frame vóór de simulatietick naar buiten
  // geprojecteerd, zodat het midden van het brein leeg blijft ("waar de robot leeft") —
  // dit vangt ook nieuwe memories die de simulatie in het centrum laat spawnen
  let cavityRadius = 0;
  const clampCavity = (): void => {
    if (cavityRadius <= 0) return;
    for (const n of byId.values()) {
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      const z = n.z ?? 0;
      const d = Math.sqrt(x * x + y * y + z * z);
      if (d >= cavityRadius || d === 0) continue;
      const k = cavityRadius / Math.max(d, 1);
      n.x = x * k;
      n.y = y * k;
      n.z = z * k;
    }
  };

  // spark-hulpjes: link-lookup op id-paar (de simulatie muteert source/target
  // van string naar node-object, dus we normaliseren bij het lezen)
  type SparkLink = {
    source: string | { id: string };
    target: string | { id: string };
    __sparkColor?: string;
    __sparkWidth?: number;
  };
  const idOf = (v: string | { id: string }): string => (typeof v === 'object' ? v.id : v);
  const pairKey = (a: string, b: string): string => (a < b ? `${a}|${b}` : `${b}|${a}`);
  let linkMap: Map<string, SparkLink> | null = null;
  const linkLookup = (): Map<string, SparkLink> => {
    if (!linkMap) {
      linkMap = new Map();
      for (const l of (graph.graphData() as GraphData).links as SparkLink[]) {
        linkMap.set(pairKey(idOf(l.source), idOf(l.target)), l);
      }
    }
    return linkMap;
  };
  const emitter = () => graph as unknown as { emitParticle?: (l: unknown) => void };

  const applyForces = (cfg: Parameters<KnowledgeGraph['setForces']>[0]): void => {
    cavityRadius = cfg.cavity;
    const g = graph as unknown as {
      d3Force(name: string, force: unknown): void;
      d3ReheatSimulation?: () => void;
      resetCountdown?: () => void;
    };
    g.d3Force('radial', cfg.radial === null ? null : forceRadial(cfg.radial).strength(cfg.radialStrength ?? 0.9));
    g.d3Force('flatten', cfg.y === null ? null : forceY(cfg.y).strength(cfg.yStrength));
    g.d3ReheatSimulation?.();
    g.resetCountdown?.();
  };

  return {
    object: graph,
    nodeIds: () => (graph.graphData() as GraphData).nodes.map((n) => n.id),
    addMemories(nodes, links) {
      for (const n of nodes) byId.set(n.id, n);
      for (const l of links) addEdge(l.source, l.target);
      linkMap = null; // lookup opnieuw opbouwen bij eerstvolgende spark
      const current = graph.graphData() as GraphData;
      graph.graphData({
        nodes: [...current.nodes, ...nodes],
        links: [...current.links, ...links],
      });
    },
    nodeCount: () => (graph.graphData() as GraphData).nodes.length,
    tick: () => {
      clampCavity();
      graph.tickFrame();
      if (!ticked) {
        ticked = true;
        if (pendingForces) {
          applyForces(pendingForces);
          pendingForces = null;
        }
      }
    },
    setForces(cfg) {
      if (!ticked) {
        pendingForces = cfg;
        return;
      }
      applyForces(cfg);
    },
    getNode: (id) => byId.get(id),
    neighbors: (id) =>
      [...(adjacency.get(id) ?? [])].map((nid) => byId.get(nid)).filter((n): n is PositionedNode => !!n),
    ready: () => ticked,
    spark: (a, b, color, width) => {
      const link = linkLookup().get(pairKey(a, b));
      if (!link || !emitter().emitParticle) return false;
      link.__sparkColor = color;
      link.__sparkWidth = width;
      emitter().emitParticle!(link);
      return true;
    },
    setNodeStyle: (style) => {
      if (style === nodeStyle) return;
      nodeStyle = style;
      graph.nodeThreeObject(nodeSprite); // her-toewijzen -> sprites opnieuw opgebouwd
    },
    sparkRandom: (color) => {
      const links = (graph.graphData() as GraphData).links as SparkLink[];
      if (links.length === 0 || !emitter().emitParticle) return;
      const link = links[Math.floor(Math.random() * links.length)]!;
      link.__sparkColor = color;
      emitter().emitParticle!(link);
    },
  };
}
