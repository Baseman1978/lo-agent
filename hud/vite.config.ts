import { defineConfig, type Plugin } from 'vite';

// three-forcegraph's dist "prefereert" window.THREE als die bestaat. Op de
// LO-pagina IS die er (vendored r128 voor de klassieke weergave), waardoor de
// graaf r128-objecten bouwt die niet mengen met onze Three 0.185
// (removeFromParent-crash). Deze transform dwingt de eigen import af.
function forceLocalThree(): Plugin {
  return {
    name: 'force-local-three',
    transform(code, id) {
      if (!id.includes('three-forcegraph')) return null;
      return code.replaceAll('window.THREE ? window.THREE', 'false ? undefined');
    },
  };
}

// Bundelt de NEBULA-HUD naar de statische map van de span-server. De output
// wordt GECOMMIT zodat Docker/deploy geen node nodig heeft; CI herbouwt en
// checkt op drift (git diff --exit-code).
export default defineConfig({
  plugins: [forceLocalThree()],
  build: {
    lib: {
      entry: 'src/main.ts',
      formats: ['es'],
      fileName: () => 'nebula.js',
    },
    outDir: '../src/span/server/static/hud',
    emptyOutDir: true,
    target: 'es2020',
    sourcemap: false,
    minify: true,
  },
});
