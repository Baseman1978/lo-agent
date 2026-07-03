import {
  BlendFunction,
  BloomEffect,
  ChromaticAberrationEffect,
  DepthOfFieldEffect,
  EffectComposer,
  EffectPass,
  NoiseEffect,
  RenderPass,
  ScanlineEffect,
  SMAAEffect,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
} from 'postprocessing';
import * as THREE from 'three';

export interface PostChain {
  composer: EffectComposer;
  /** toggle the cosmetic effects for adaptive quality */
  setCosmetics(enabled: boolean): void;
  /** cinema-look (scherptediepte, aberratie, scanlines, korrel) aan/uit —
      uit = kraakhelder beeld; bloom/SMAA/tonemapping blijven altijd aan */
  setCinema(on: boolean): void;
  /** cinematografische focus (DoF) volgt dit punt — meestal de orb of de geselecteerde node */
  setFocus(target: THREE.Vector3): void;
}

/**
 * Hologram aesthetic per the research recommendation: pmndrs `postprocessing`
 * with a single merged EffectPass. Cosmetic effects are toggled via
 * BlendFunction.SKIP instead of disabling the pass — the last pass must stay
 * enabled or the composer never blits to screen.
 */
export function createPostChain(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.Camera,
  lite = false
): PostChain {
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));

  const bloom = new BloomEffect({
    intensity: 1.15,
    luminanceThreshold: 0.18,
    luminanceSmoothing: 0.25,
    mipmapBlur: true,
  });

  const aberration = new ChromaticAberrationEffect({
    offset: new THREE.Vector2(0.0009, 0.0006),
    radialModulation: true,
    modulationOffset: 0.4,
  });
  const aberrationBlend = aberration.blendMode.blendFunction;

  const scanlines = new ScanlineEffect({ density: 1.1 });
  scanlines.blendMode.opacity.value = 0.06;
  const scanlineBlend = scanlines.blendMode.blendFunction;

  const vignette = new VignetteEffect({ offset: 0.28, darkness: 0.6 });

  // stap A "fijnheid": SMAA (MSAA staat uit) + ACES filmic tone mapping —
  // fijne deeltjes/lijnen worden strak en additive licht clipt niet meer hard naar wit
  const smaa = new SMAAEffect();
  const toneMapping = new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC });

  // stap C "cinema": depth-of-field met focus op de orb + subtiele film grain
  const dof = new DepthOfFieldEffect(camera, {
    worldFocusDistance: 620,
    worldFocusRange: 520,
    bokehScale: 2.2,
  });
  const grain = new NoiseEffect({ blendFunction: BlendFunction.OVERLAY, premultiply: false });
  grain.blendMode.opacity.value = 0.07;

  // SMAA en DoF krijgen eigen passes (convolutie-effecten mengen niet met
  // chromatic aberration). De laatste pass blijft altijd enabled (screen-blit).
  const smaaPass = new EffectPass(camera, smaa);
  const dofPass = new EffectPass(camera, dof);
  composer.addPass(smaaPass);
  if (lite) {
    // mobiel/zwakke GPU: bloom + vignet + tonemapping volstaan; DoF (duurste),
    // aberratie, scanlines en grain blijven uit
    composer.addPass(new EffectPass(camera, bloom, vignette, toneMapping));
  } else {
    composer.addPass(dofPass);
    composer.addPass(new EffectPass(camera, bloom, aberration, scanlines, vignette, grain, toneMapping));
  }

  let cinema = true;
  const grainBlend = grain.blendMode.blendFunction;
  const applyLook = (enabled: boolean): void => {
    aberration.blendMode.setBlendFunction(enabled ? aberrationBlend : BlendFunction.SKIP);
    scanlines.blendMode.setBlendFunction(enabled ? scanlineBlend : BlendFunction.SKIP);
    grain.blendMode.setBlendFunction(enabled ? grainBlend : BlendFunction.SKIP);
    dofPass.enabled = enabled;
  };
  return {
    composer,
    setCosmetics(enabled: boolean) {
      if (lite) return; // lite draait al minimaal
      applyLook(enabled && cinema); // adaptieve kwaliteit respecteert de cinema-keuze
    },
    setCinema(on: boolean) {
      cinema = on;
      if (!lite) applyLook(on);
    },
    setFocus(target: THREE.Vector3) {
      if (dof.target) dof.target.copy(target);
      else dof.target = target.clone();
    },
  };
}
