// Hover-tooltip + klik-detailpaneel voor de NEBULA in LO. Geport uit de
// sandbox; verschillen: pointer-coördinaten relatief aan het canvas (de scene
// is hier géén fullscreen), LO-types i.p.v. dataklassen, en een deel-knop
// die het gedrag van het bestaande hologram volgt (/api/share).

import * as THREE from 'three';
import type { PositionedNode } from './data/synthetic';
import type { Highlighter } from './scene/fx';
import type { KnowledgeGraph } from './scene/graph';

function nodeFromObject(obj: THREE.Object3D | null): PositionedNode | null {
  let cur: THREE.Object3D | null = obj;
  while (cur) {
    const data = (cur as { __data?: PositionedNode }).__data;
    if (data && typeof data.id === 'string') return data;
    cur = cur.parent;
  }
  return null;
}

const esc = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function ensureEl(id: string): HTMLElement {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    document.body.appendChild(el);
  }
  return el;
}

export interface Interactions {
  update(t: number): void;
  dispose(): void;
}

export function setupInteractions(opts: {
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  graph: KnowledgeGraph;
  highlighter: Highlighter;
  typeOf(id: string): string;
  authHeaders?: () => Record<string, string>;
  onFocusNode(position: THREE.Vector3): void;
  onSelectionChange(node: PositionedNode | null): void;
}): Interactions {
  const { renderer, camera, graph, highlighter } = opts;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let pointerDirty = false;
  let hovered: PositionedNode | null = null;
  let selected: PositionedNode | null = null;
  let downAt: [number, number] | null = null;

  const tooltip = ensureEl('nebula-tooltip');
  const panel = ensureEl('nebula-detail');

  const pick = (): PositionedNode | null => {
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObject(graph.object, true);
    for (const h of hits) {
      const node = nodeFromObject(h.object);
      if (node) return node;
    }
    return null;
  };

  const onMove = (e: PointerEvent): void => {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.set(
      ((e.clientX - r.left) / r.width) * 2 - 1,
      -((e.clientY - r.top) / r.height) * 2 + 1
    );
    pointerDirty = true;
    tooltip.style.left = `${e.clientX + 16}px`;
    tooltip.style.top = `${e.clientY + 14}px`;
  };
  const onDown = (e: PointerEvent): void => {
    downAt = [e.clientX, e.clientY];
  };
  const onUp = (e: PointerEvent): void => {
    if (!downAt) return;
    const moved = Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]);
    downAt = null;
    if (moved > 6) return;
    select(hovered);
  };
  renderer.domElement.addEventListener('pointermove', onMove);
  renderer.domElement.addEventListener('pointerdown', onDown);
  renderer.domElement.addEventListener('pointerup', onUp);

  function select(node: PositionedNode | null): void {
    selected = node;
    highlighter.select(node, node ? graph.neighbors(node.id) : []);
    if (node) {
      const buren = graph.neighbors(node.id);
      const type = opts.typeOf(node.id) || node.dataClass;
      const burenHtml = buren
        .slice(0, 5)
        .map((b) => `<div class="det-nb">· ${esc(b.label)}</div>`)
        .join('');
      panel.innerHTML =
        `<div class="det-title">${esc(node.label)}</div>` +
        `<div class="det-row"><span class="chip" style="background:${node.color}"></span>${esc(type)}</div>` +
        `<div class="det-row">verbindingen · <b>${buren.length}</b></div>` +
        burenHtml +
        `<button class="det-share">⇪ deel met team</button>` +
        `<div class="det-hint">klik op lege ruimte om te sluiten</div>`;
      const sb = panel.querySelector('.det-share') as HTMLButtonElement | null;
      if (sb) {
        sb.onclick = () => {
          sb.disabled = true;
          fetch('/api/share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(opts.authHeaders?.() ?? {}) },
            body: JSON.stringify({ id: node.id }),
          })
            .then(async (r) => ({ ok: r.ok, d: await r.json().catch(() => ({})) }))
            .then(({ ok, d }) => {
              sb.textContent = ok ? '✓ gedeeld met team' : `✗ ${(d as { detail?: string }).detail ?? 'mislukt'}`;
            })
            .catch(() => { sb.textContent = '✗ mislukt'; });
        };
      }
      panel.classList.add('open');
      opts.onFocusNode(new THREE.Vector3(node.x ?? 0, node.y ?? 0, node.z ?? 0));
    } else {
      panel.classList.remove('open');
    }
    opts.onSelectionChange(node);
  }

  return {
    update(t: number) {
      if (pointerDirty) {
        pointerDirty = false;
        hovered = pick();
        highlighter.setHover(hovered);
        if (hovered) {
          tooltip.textContent = `${hovered.label} — ${opts.typeOf(hovered.id) || hovered.dataClass}`;
          tooltip.classList.add('open');
          renderer.domElement.style.cursor = 'pointer';
        } else {
          tooltip.classList.remove('open');
          renderer.domElement.style.cursor = 'grab';
        }
      }
      highlighter.update(t);
    },
    dispose() {
      renderer.domElement.removeEventListener('pointermove', onMove);
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('pointerup', onUp);
      tooltip.remove();
      panel.remove();
    },
  };
}
