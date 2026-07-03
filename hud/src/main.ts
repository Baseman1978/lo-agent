// NEBULA-HUD — N0: build-fundament.
// Deze fase bewijst de pijplijn (Vite-bundel, eigen Three 0.185 naast de
// vendored r128, CSP-conform, WebGL2) met een minimale placeholder-scene:
// een langzaam draaiende wireframe-icosaëder + sterrenveld in de LO-kleuren.
// De echte orb + geheugenwolk (sandbox-port) komt in N1.

import * as THREE from 'three';

export interface NebulaHandle {
  unmount(): void;
}

export function webgl2Available(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch {
    return false;
  }
}

export function mount(container: HTMLElement): NebulaHandle {
  const renderer = new THREE.WebGLRenderer({
    powerPreference: 'high-performance',
    antialias: true,
    alpha: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, 1, 1, 4000);
  camera.position.set(0, 40, 420);
  camera.lookAt(0, 0, 0);

  // placeholder-orb: wireframe-icosaëder met kern, in LO-cyan
  const shell = new THREE.Mesh(
    new THREE.IcosahedronGeometry(110, 2),
    new THREE.MeshBasicMaterial({
      color: 0x38e1ff,
      wireframe: true,
      transparent: true,
      opacity: 0.28,
    })
  );
  scene.add(shell);
  const core = new THREE.Mesh(
    new THREE.IcosahedronGeometry(26, 3),
    new THREE.MeshBasicMaterial({
      color: 0xe0f2fe,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  scene.add(core);

  // sterrenveld: 1500 zachte punten als voorproef van de geheugenwolk
  const N = 1500;
  const pos = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const r = 180 + Math.random() * 420;
    const th = Math.random() * Math.PI * 2;
    const y = (Math.random() - 0.5) * 2;
    const s = Math.sqrt(Math.max(0, 1 - y * y));
    pos[i * 3] = Math.cos(th) * s * r;
    pos[i * 3 + 1] = y * r * 0.7;
    pos[i * 3 + 2] = Math.sin(th) * s * r;
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const stars = new THREE.Points(
    starGeo,
    new THREE.PointsMaterial({
      color: 0x67e8f9,
      size: 2.2,
      transparent: true,
      opacity: 0.55,
      sizeAttenuation: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  scene.add(stars);

  const resize = (): void => {
    const w = Math.max(1, container.clientWidth);
    const h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();

  // verborgen tab -> volledig stil (Fase D-lijn: geen GPU/CPU op de achtergrond)
  const t0 = performance.now();
  renderer.setAnimationLoop(() => {
    if (document.hidden) return;
    const t = (performance.now() - t0) / 1000;
    shell.rotation.y = t * 0.12;
    shell.rotation.x = Math.sin(t * 0.07) * 0.18;
    stars.rotation.y = -t * 0.02;
    core.scale.setScalar(1 + 0.05 * Math.sin(t * 1.4));
    renderer.render(scene, camera);
  });

  return {
    unmount() {
      renderer.setAnimationLoop(null);
      ro.disconnect();
      renderer.dispose();
      starGeo.dispose();
      shell.geometry.dispose();
      core.geometry.dispose();
      renderer.domElement.remove();
    },
  };
}
