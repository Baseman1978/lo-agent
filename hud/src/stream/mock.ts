// Mock of the future Fastify WebSocket stream (agent-state / new memories).
// Demonstrates the event-buffer/drain pattern from the research: socket callbacks
// only enqueue; the render loop drains at frame start, so parsing/layout work
// never blocks a frame mid-render. No real backend is involved.

import { makeNode, rng, type MemoryLink, type MemoryNode } from '../data/synthetic';

export interface MemoryEvent {
  node: MemoryNode;
  links: MemoryLink[];
}

export class MockMemoryStream {
  private buffer: MemoryEvent[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private rand = rng(0xbeef);
  private nextId: number;

  constructor(
    private existingIds: () => string[],
    startIndex: number,
    private intervalMs = 2500
  ) {
    this.nextId = startIndex;
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      // simulates a WebSocket "message" arriving: enqueue only, never touch the scene
      const node = makeNode(this.nextId++, this.rand);
      const ids = this.existingIds();
      const links: MemoryLink[] = [];
      const linkCount = 1 + Math.floor(this.rand() * 2);
      for (let i = 0; i < linkCount && ids.length > 0; i++) {
        const target = ids[Math.floor(Math.pow(this.rand(), 2) * ids.length)];
        if (target) links.push({ source: node.id, target });
      }
      this.buffer.push({ node, links });
    }, this.intervalMs);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Called once per animation frame: returns and clears everything queued. */
  drain(): MemoryEvent[] {
    if (this.buffer.length === 0) return [];
    const out = this.buffer;
    this.buffer = [];
    return out;
  }
}
