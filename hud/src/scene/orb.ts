import * as THREE from 'three';
import { GPUComputationRenderer, type Variable } from 'three/addons/misc/GPUComputationRenderer.js';
import { STATE_COLOR, type AgentState } from '../state/agent';
import { SNOISE } from './shaders/noise';

// De agent-orb naar het voorbeeld van de Lost in Space-robot (Netflix):
//  - stap B "flow": een GPGPU curl-noise-simulatie — 65k deeltjes STROMEN langs een
//    divergentievrij noise-veld (zijdeachtig, "lava lamp"), posities leven in GPU-textures
//  - een alien-tech ENERGIESCHIL met organisch vloeiende lichtaders
//  - optionele ripple-schillen (tuning-paneel)

const DEFAULT_SIM_SIZE = 256; // 256×256 = 65.536 deeltjes (lite: 128²)
const FLOW_SCALE = 60; // BEHAVIOUR.flow → wereldeenheden per seconde

// --- GPGPU-positiesimulatie ---------------------------------------------------
const POSITION_FRAG = /* glsl */ `
${SNOISE}
vec3 snoise3(vec3 p) {
  return vec3(snoise(p), snoise(p + vec3(123.4, 57.1, 89.2)), snoise(p + vec3(199.7, -43.3, 71.9)));
}
// curl van het noise-veld: divergentievrij → deeltjes stromen zonder te klonteren
vec3 curl(vec3 p) {
  const float e = 0.35;
  vec3 dx = snoise3(p + vec3(e, 0.0, 0.0)) - snoise3(p - vec3(e, 0.0, 0.0));
  vec3 dy = snoise3(p + vec3(0.0, e, 0.0)) - snoise3(p - vec3(0.0, e, 0.0));
  vec3 dz = snoise3(p + vec3(0.0, 0.0, e)) - snoise3(p - vec3(0.0, 0.0, e));
  return vec3(dy.z - dz.y, dz.x - dx.z, dx.y - dy.x) / (2.0 * e);
}
uniform float uTime, uDt, uFlowSpeed, uNoiseFreq, uRadius, uDensity, uSwirl;
void main() {
  vec2 uv = gl_FragCoord.xy / resolution.xy;
  vec4 data = texture2D(texturePosition, uv);
  vec3 pos = data.xyz;
  float seed = data.w;
  // stroming langs het curl-veld (traag scrollend door de tijd)
  vec3 vel = curl(pos * uNoiseFreq + vec3(0.0, uTime * 0.05, 0.0)) * uFlowSpeed;
  // zachte werveling rond de Y-as
  vel += cross(vec3(0.0, 1.0, 0.0), normalize(pos)) * uSwirl * uRadius;
  // veer naar de eigen bol-schil: houdt de wolk bolvormig zonder de stroming te doden
  float home = uRadius * uDensity * (0.7 + 0.3 * fract(seed * 7.31));
  float d = max(length(pos), 1e-4);
  vel += (pos / d) * (home - d) * 2.2;
  pos += vel * uDt;
  gl_FragColor = vec4(pos, seed);
}
`;

// --- render: points lezen hun positie uit de simulatietexture ------------------
// trail-simulatie: een exponentieel "achterlopende" kopie van elke positie —
// het lijnstuk tussen positie en trail-positie wordt een zijdeachtige streep
const TRAIL_FRAG = /* glsl */ `
uniform float uTrailLag;
void main() {
  vec2 uv = gl_FragCoord.xy / resolution.xy;
  vec3 pos = texture2D(texturePosition, uv).xyz;
  vec3 trail = texture2D(textureTrail, uv).xyz;
  gl_FragColor = vec4(mix(trail, pos, uTrailLag), 1.0);
}
`;

const LINE_VERT = /* glsl */ `
uniform sampler2D uPosTex;
uniform sampler2D uTrailTex;
attribute float aEnd;
varying float vFade;
void main() {
  vec3 p0 = texture2D(uPosTex, uv).xyz;
  vec3 p1 = texture2D(uTrailTex, uv).xyz;
  vec3 p = mix(p0, p1, aEnd);
  // alleen bewegende deeltjes trekken een streep; staart doft uit
  float speed = length(p0 - p1);
  vFade = smoothstep(1.2, 9.0, speed) * (1.0 - aEnd * 0.75);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}
`;

const LINE_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying float vFade;
void main() {
  gl_FragColor = vec4(uColor, vFade * uOpacity);
}
`;

const CLOUD_VERT = /* glsl */ `
uniform sampler2D uPosTex;
uniform float uTime, uSize, uFlash, uWaveAmp, uWaveSpeed, uContract;
varying float vI;
varying float vFlash;
void main() {
  vec4 data = texture2D(uPosTex, uv);
  vec3 pos = data.xyz;
  float seed = data.w;
  vec3 dir = normalize(pos);
  // luister-banden en spreek-golven als lichte displacement bovenop de stroming
  pos *= 1.0 - uContract * (0.5 + 0.5 * sin(dir.y * 9.0 - uTime * 4.0));
  pos *= 1.0 + uWaveAmp * sin(uTime * uWaveSpeed - fract(seed * 7.31) * 6.2831 + seed * 6.2831);
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  float tw = 0.55 + 0.45 * sin(uTime * (1.5 + fract(seed * 3.7) * 3.0) + seed * 40.0);
  vI = tw;
  float grp = fract(seed + floor(uTime * 7.0) * 0.61803);
  vFlash = step(1.0 - 0.025 * uFlash, grp);
  gl_PointSize = clamp(uSize * (0.7 + 0.3 * tw) * (680.0 / -mv.z) * (1.0 + vFlash * 2.2), 1.0, 7.0);
  gl_Position = projectionMatrix * mv;
}
`;

const CLOUD_FRAG = /* glsl */ `
uniform vec3 uColor;
varying float vI;
varying float vFlash;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.05, d);
  vec3 c = uColor * (0.8 + 0.5 * vI) + (uColor * 1.7 + vec3(0.12)) * vFlash;
  gl_FragColor = vec4(c, min(1.0, a * (0.22 + 0.38 * vI) + vFlash * 0.35));
}
`;

const VEINS_VERT = /* glsl */ `
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vDir = normalize(position);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`;

const VEINS_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uTime, uOpacity, uVeinSpeed;
varying vec3 vNormal;
varying vec3 vView;
varying vec3 vDir;
${SNOISE}
void main() {
  vec3 q = vDir * 2.5 + vec3(0.0, uTime * 0.06 * uVeinSpeed, 0.0);
  vec3 w = vec3(fbm(q), fbm(q + vec3(5.2)), fbm(q + vec3(9.7)));
  float n = fbm(vDir * 3.5 + 1.4 * w + vec3(uTime * 0.045 * uVeinSpeed));
  float ridge = smoothstep(0.36, 0.5, n) * (1.0 - smoothstep(0.5, 0.66, n));
  float fres = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 1.3);
  float pulse = 0.8 + 0.2 * sin(uTime * 1.1);
  float a = ridge * (0.4 + 0.7 * fres) * uOpacity * pulse * 1.5;
  gl_FragColor = vec4(uColor * 1.9, a);
}
`;

const RIPPLE_VERT = /* glsl */ `
varying vec3 vNormal;
varying vec3 vView;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vView = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}
`;

const RIPPLE_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying vec3 vNormal;
varying vec3 vView;
void main() {
  float fresnel = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.6);
  gl_FragColor = vec4(uColor, fresnel * uOpacity);
}
`;

interface CloudParams {
  noiseFreq: number; // schaal van het curl-veld (hoger = fijnere wervels)
  flow: number; // stroomsnelheid (×FLOW_SCALE wereldeenheden/s)
  swirl: number;
  waveAmp: number;
  waveSpeed: number;
  contract: number;
  size: number;
  veinOpacity: number;
  veinSpeed: number;
  rippleEvery: number; // ms, 0 = geen
  stateFlash: number;
  stateDensity: number;
}

/** gebruikersinstellingen uit het tuning-paneel (toets O) */
export interface OrbSettings {
  density: number;
  flash: number;
  veinBoost: number;
  ripples: boolean;
}

export const DEFAULT_ORB_SETTINGS: OrbSettings = { density: 1, flash: 0.15, veinBoost: 1, ripples: false };

// gedrag per status — de "gezichtsuitdrukkingen" van de robot
const BEHAVIOUR: Record<AgentState, CloudParams> = {
  idle: { noiseFreq: 1.6, flow: 0.12, swirl: 0.05, waveAmp: 0, waveSpeed: 0, contract: 0, size: 1.7, veinOpacity: 0.7, veinSpeed: 1, rippleEvery: 0, stateFlash: 0, stateDensity: 1 },
  listening: { noiseFreq: 2.2, flow: 0.2, swirl: 0.12, waveAmp: 0.02, waveSpeed: 5, contract: 0.09, size: 1.8, veinOpacity: 0.95, veinSpeed: 1.6, rippleEvery: 950, stateFlash: 0.1, stateDensity: 0.97 },
  thinking: { noiseFreq: 3.1, flow: 0.6, swirl: 0.5, waveAmp: 0, waveSpeed: 0, contract: 0, size: 1.95, veinOpacity: 1.15, veinSpeed: 2.6, rippleEvery: 0, stateFlash: 0.25, stateDensity: 1 },
  speaking: { noiseFreq: 2.0, flow: 0.25, swirl: 0.1, waveAmp: 0.11, waveSpeed: 9, contract: 0, size: 1.9, veinOpacity: 1, veinSpeed: 1.8, rippleEvery: 430, stateFlash: 0.15, stateDensity: 1 },
  alert: { noiseFreq: 3.6, flow: 0.7, swirl: 0.3, waveAmp: 0.07, waveSpeed: 13, contract: 0.06, size: 2.0, veinOpacity: 1.35, veinSpeed: 3.4, rippleEvery: 520, stateFlash: 0.5, stateDensity: 0.85 },
};

interface Ripple {
  mesh: THREE.Mesh;
  material: THREE.ShaderMaterial;
  age: number;
  life: number;
}

export interface Orb {
  group: THREE.Group;
  setScale(s: number): void;
  setSettings(s: OrbSettings): void;
  update(t: number, dtMs: number, state: AgentState): void;
}

export function createOrb(renderer: THREE.WebGLRenderer, radius = 110, simSize = DEFAULT_SIM_SIZE): Orb {
  const SIM_SIZE = simSize;
  const group = new THREE.Group();
  const color = new THREE.Color(STATE_COLOR.idle);
  const targetColor = new THREE.Color(STATE_COLOR.idle);
  const params: CloudParams = { ...BEHAVIOUR.idle };
  let settings: OrbSettings = { ...DEFAULT_ORB_SETTINGS };
  const cloudRadius = radius * 0.86;

  // --- GPGPU-simulatie ---------------------------------------------------------
  const gpu = new GPUComputationRenderer(SIM_SIZE, SIM_SIZE, renderer);
  const posTex0 = gpu.createTexture();
  {
    const arr = posTex0.image.data as Float32Array;
    const golden = Math.PI * (3 - Math.sqrt(5));
    const count = SIM_SIZE * SIM_SIZE;
    for (let i = 0; i < count; i++) {
      const y = 1 - (i / (count - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const th = golden * i;
      const seed = Math.random();
      const home = cloudRadius * (0.7 + 0.3 * ((seed * 7.31) % 1));
      arr[i * 4] = Math.cos(th) * r * home;
      arr[i * 4 + 1] = y * home;
      arr[i * 4 + 2] = Math.sin(th) * r * home;
      arr[i * 4 + 3] = seed;
    }
  }
  const posVar: Variable = gpu.addVariable('texturePosition', POSITION_FRAG, posTex0);
  const trailTex0 = gpu.createTexture();
  (trailTex0.image.data as Float32Array).set(posTex0.image.data as Float32Array);
  const trailVar: Variable = gpu.addVariable('textureTrail', TRAIL_FRAG, trailTex0);
  gpu.setVariableDependencies(posVar, [posVar]);
  gpu.setVariableDependencies(trailVar, [posVar, trailVar]);
  Object.assign(posVar.material.uniforms, {
    uTime: { value: 0 },
    uDt: { value: 0.016 },
    uFlowSpeed: { value: params.flow * FLOW_SCALE },
    uNoiseFreq: { value: params.noiseFreq / radius },
    uRadius: { value: cloudRadius },
    uDensity: { value: 1 },
    uSwirl: { value: params.swirl },
  });
  Object.assign(trailVar.material.uniforms, {
    uTrailLag: { value: 0.12 },
  });
  const gpuError = gpu.init();
  if (gpuError !== null) console.error('GPGPU init faalde:', gpuError);

  // --- points-geometrie: elk deeltje wijst naar zijn texel in de simulatie ------
  const count = SIM_SIZE * SIM_SIZE;
  const cloudGeo = new THREE.BufferGeometry();
  cloudGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  const uvs = new Float32Array(count * 2);
  for (let i = 0; i < count; i++) {
    uvs[i * 2] = ((i % SIM_SIZE) + 0.5) / SIM_SIZE;
    uvs[i * 2 + 1] = (Math.floor(i / SIM_SIZE) + 0.5) / SIM_SIZE;
  }
  cloudGeo.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  cloudGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), radius * 1.8);

  const cloudMat = new THREE.ShaderMaterial({
    vertexShader: CLOUD_VERT,
    fragmentShader: CLOUD_FRAG,
    uniforms: {
      uPosTex: { value: null },
      uTime: { value: 0 },
      uColor: { value: color.clone() },
      uSize: { value: params.size },
      uFlash: { value: 0 },
      uWaveAmp: { value: params.waveAmp },
      uWaveSpeed: { value: params.waveSpeed },
      uContract: { value: params.contract },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const points = new THREE.Points(cloudGeo, cloudMat);
  points.frustumCulled = false;
  group.add(points);

  // --- trail-strepen: één lijnstuk per deeltje, van positie naar trail-positie ---
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 2 * 3), 3));
  const lineUvs = new Float32Array(count * 2 * 2);
  const lineEnds = new Float32Array(count * 2);
  for (let i = 0; i < count; i++) {
    const u = ((i % SIM_SIZE) + 0.5) / SIM_SIZE;
    const v = (Math.floor(i / SIM_SIZE) + 0.5) / SIM_SIZE;
    lineUvs.set([u, v, u, v], i * 4);
    lineEnds[i * 2] = 0;
    lineEnds[i * 2 + 1] = 1;
  }
  lineGeo.setAttribute('uv', new THREE.BufferAttribute(lineUvs, 2));
  lineGeo.setAttribute('aEnd', new THREE.BufferAttribute(lineEnds, 1));
  lineGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), radius * 1.8);
  const lineMat = new THREE.ShaderMaterial({
    vertexShader: LINE_VERT,
    fragmentShader: LINE_FRAG,
    uniforms: {
      uPosTex: { value: null },
      uTrailTex: { value: null },
      uColor: { value: color.clone() },
      uOpacity: { value: 0.1 },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const trails = new THREE.LineSegments(lineGeo, lineMat);
  trails.frustumCulled = false;
  group.add(trails);

  // --- alien-tech aderschil ------------------------------------------------------
  const veinsMat = new THREE.ShaderMaterial({
    vertexShader: VEINS_VERT,
    fragmentShader: VEINS_FRAG,
    uniforms: {
      uColor: { value: color.clone() },
      uTime: { value: 0 },
      uOpacity: { value: params.veinOpacity },
      uVeinSpeed: { value: params.veinSpeed },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  group.add(new THREE.Mesh(new THREE.IcosahedronGeometry(radius * 1.12, 5), veinsMat));

  // --- kern ------------------------------------------------------------------------
  const coreMat = new THREE.MeshBasicMaterial({
    color: 0xe0f2fe,
    transparent: true,
    opacity: 0.9,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(radius * 0.22, 3), coreMat);
  group.add(core);

  // --- ripple-schillen (optioneel via tuning) ---------------------------------------
  const rippleGeo = new THREE.IcosahedronGeometry(radius, 3);
  const ripples: Ripple[] = [];
  let sinceRipple = 0;
  let currentState: AgentState = 'idle';

  const spawnRipple = () => {
    if (ripples.length >= 6) return;
    const material = new THREE.ShaderMaterial({
      vertexShader: RIPPLE_VERT,
      fragmentShader: RIPPLE_FRAG,
      uniforms: { uColor: { value: targetColor.clone() }, uOpacity: { value: 0.5 } },
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(rippleGeo, material);
    mesh.scale.setScalar(1.05);
    group.add(mesh);
    ripples.push({ mesh, material, age: 0, life: 1600 });
  };

  const lerp = (a: number, b: number, f: number) => a + (b - a) * f;
  let density = 1;
  let flash = 0;

  return {
    group,
    setScale: (s) => group.scale.setScalar(s),
    setSettings: (s) => {
      settings = { ...s };
    },
    update(t, dtMs, state) {
      if (state !== currentState) {
        currentState = state;
        targetColor.set(STATE_COLOR[state]);
      }
      const target = BEHAVIOUR[state];
      const f = Math.min(1, dtMs / 350);
      params.noiseFreq = lerp(params.noiseFreq, target.noiseFreq, f);
      params.flow = lerp(params.flow, target.flow, f);
      params.swirl = lerp(params.swirl, target.swirl, f);
      params.waveAmp = lerp(params.waveAmp, target.waveAmp, f);
      params.waveSpeed = lerp(params.waveSpeed, target.waveSpeed, f);
      params.contract = lerp(params.contract, target.contract, f);
      params.size = lerp(params.size, target.size, f);
      params.veinOpacity = lerp(params.veinOpacity, target.veinOpacity, f);
      params.veinSpeed = lerp(params.veinSpeed, target.veinSpeed, f);
      density = lerp(density, settings.density * target.stateDensity, f);
      flash = lerp(flash, Math.min(1, settings.flash + target.stateFlash), f);
      color.lerp(targetColor, 0.06);

      // simulatiestap
      const su = posVar.material.uniforms;
      su.uTime!.value = t;
      su.uDt!.value = Math.min(dtMs / 1000, 0.05);
      su.uFlowSpeed!.value = params.flow * FLOW_SCALE;
      su.uNoiseFreq!.value = params.noiseFreq / radius;
      su.uDensity!.value = density;
      su.uSwirl!.value = params.swirl;
      gpu.compute();

      // renderstap
      const posTexture = gpu.getCurrentRenderTarget(posVar).texture;
      const u = cloudMat.uniforms;
      u.uPosTex!.value = posTexture;
      lineMat.uniforms.uPosTex!.value = posTexture;
      lineMat.uniforms.uTrailTex!.value = gpu.getCurrentRenderTarget(trailVar).texture;
      (lineMat.uniforms.uColor!.value as THREE.Color).copy(color);
      u.uTime!.value = t;
      (u.uColor!.value as THREE.Color).copy(color);
      u.uSize!.value = params.size;
      u.uFlash!.value = flash;
      u.uWaveAmp!.value = params.waveAmp;
      u.uWaveSpeed!.value = params.waveSpeed;
      u.uContract!.value = params.contract;

      veinsMat.uniforms.uTime!.value = t;
      (veinsMat.uniforms.uColor!.value as THREE.Color).copy(color);
      veinsMat.uniforms.uOpacity!.value = params.veinOpacity * settings.veinBoost;
      veinsMat.uniforms.uVeinSpeed!.value = params.veinSpeed;

      const beat =
        state === 'speaking'
          ? 0.1 * Math.abs(Math.sin(t * 9.0) * 0.5 + Math.sin(t * 13.7) * 0.3 + Math.sin(t * 23.0) * 0.2)
          : 0.03 * Math.sin(t * (state === 'thinking' ? 8 : state === 'alert' ? 13 : 1.4));
      core.scale.setScalar(1 + beat);

      sinceRipple += dtMs;
      if (settings.ripples && target.rippleEvery > 0 && sinceRipple > target.rippleEvery) {
        sinceRipple = 0;
        spawnRipple();
      }
      for (let i = ripples.length - 1; i >= 0; i--) {
        const r = ripples[i]!;
        r.age += dtMs;
        const p = r.age / r.life;
        if (p >= 1) {
          group.remove(r.mesh);
          r.material.dispose();
          ripples.splice(i, 1);
          continue;
        }
        r.mesh.scale.setScalar(1.05 + p * 1.5);
        r.material.uniforms.uOpacity!.value = (1 - p) * 0.5;
      }
    },
  };
}
