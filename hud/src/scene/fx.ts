import * as THREE from 'three';
import type { PositionedNode } from '../data/synthetic';

/** soft radial glow sprite texture (generated once, no assets) */
export function makeGlowTexture(): THREE.Texture {
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.35, 'rgba(255,255,255,.45)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeSprite(tex: THREE.Texture, color: number, scale: number, opacity = 1): THREE.Sprite {
  const mat = new THREE.SpriteMaterial({
    map: tex,
    color,
    transparent: true,
    opacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.setScalar(scale);
  return sprite;
}

const nodePos = (n: PositionedNode) => new THREE.Vector3(n.x ?? 0, n.y ?? 0, n.z ?? 0);

interface Bolt {
  sprite: THREE.Sprite;
  target: PositionedNode;
}
interface Flash {
  sprite: THREE.Sprite;
  age: number;
  life: number;
}

/** "memory lands": a light pulse travels from the orb to the new node, then flashes. */
export class MemoryPulses {
  readonly group = new THREE.Group();
  private bolts: Bolt[] = [];
  private flashes: Flash[] = [];

  constructor(private tex: THREE.Texture) {}

  spawn(target: PositionedNode): void {
    if (this.bolts.length >= 8) return; // cap, drop excess quietly
    const sprite = makeSprite(this.tex, 0x9aeaff, 26);
    sprite.position.set(0, 0, 0); // orb centre
    this.group.add(sprite);
    this.bolts.push({ sprite, target });
  }

  update(dtMs: number): void {
    const step = Math.min(dtMs / 1000, 0.05);
    for (let i = this.bolts.length - 1; i >= 0; i--) {
      const b = this.bolts[i]!;
      const goal = nodePos(b.target);
      b.sprite.position.lerp(goal, 1 - Math.pow(0.06, step)); // frame-rate independent chase
      if (b.sprite.position.distanceTo(goal) < 10) {
        this.group.remove(b.sprite);
        b.sprite.material.dispose();
        this.bolts.splice(i, 1);
        const flash = makeSprite(this.tex, 0xffffff, 14);
        flash.position.copy(goal);
        this.group.add(flash);
        this.flashes.push({ sprite: flash, age: 0, life: 700 });
      }
    }
    for (let i = this.flashes.length - 1; i >= 0; i--) {
      const f = this.flashes[i]!;
      f.age += dtMs;
      const p = f.age / f.life;
      if (p >= 1) {
        this.group.remove(f.sprite);
        f.sprite.material.dispose();
        this.flashes.splice(i, 1);
        continue;
      }
      f.sprite.scale.setScalar(14 + p * 46);
      f.sprite.material.opacity = 1 - p;
    }
  }
}

const MAX_NEIGHBOR_LINES = 48;

/** hover marker + selection marker + glowing lines to the selected node's neighbours */
export class Highlighter {
  readonly group = new THREE.Group();
  private hover: THREE.Sprite;
  private selected: THREE.Sprite;
  private lines: THREE.LineSegments;
  private linePositions: Float32Array;
  private hoverNode: PositionedNode | null = null;
  private selNode: PositionedNode | null = null;
  private selNeighbors: PositionedNode[] = [];

  constructor(tex: THREE.Texture) {
    this.hover = makeSprite(tex, 0xe0f2fe, 22, 0.9);
    this.hover.visible = false;
    this.group.add(this.hover);

    this.selected = makeSprite(tex, 0x67e8f9, 30, 1);
    this.selected.visible = false;
    this.group.add(this.selected);

    this.linePositions = new Float32Array(MAX_NEIGHBOR_LINES * 2 * 3);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.linePositions, 3));
    this.lines = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({
        color: 0xbae6fd,
        transparent: true,
        opacity: 0.85,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    this.lines.visible = false;
    this.group.add(this.lines);
  }

  setHover(node: PositionedNode | null): void {
    this.hoverNode = node;
  }

  select(node: PositionedNode | null, neighbors: PositionedNode[]): void {
    this.selNode = node;
    this.selNeighbors = neighbors.slice(0, MAX_NEIGHBOR_LINES);
  }

  update(t: number): void {
    if (this.hoverNode && this.hoverNode !== this.selNode) {
      this.hover.visible = true;
      this.hover.position.copy(nodePos(this.hoverNode));
    } else this.hover.visible = false;

    if (this.selNode) {
      this.selected.visible = true;
      this.selected.position.copy(nodePos(this.selNode));
      this.selected.scale.setScalar(28 + Math.sin(t * 4) * 4);

      const from = nodePos(this.selNode);
      let seg = 0;
      for (const nb of this.selNeighbors) {
        const to = nodePos(nb);
        this.linePositions.set([from.x, from.y, from.z, to.x, to.y, to.z], seg * 6);
        seg++;
      }
      const attr = this.lines.geometry.getAttribute('position') as THREE.BufferAttribute;
      attr.needsUpdate = true;
      this.lines.geometry.setDrawRange(0, seg * 2);
      this.lines.visible = seg > 0;
    } else {
      this.selected.visible = false;
      this.lines.visible = false;
    }
  }
}
