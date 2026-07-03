import { defineConfig } from 'vite';

// Bundelt de NEBULA-HUD naar de statische map van de span-server. De output
// wordt GECOMMIT zodat Docker/deploy geen node nodig heeft; CI herbouwt en
// checkt op drift (git diff --exit-code).
export default defineConfig({
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
