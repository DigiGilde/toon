/**
 * The place script: a few hundred bytes of classic script (IIFE, no exports,
 * so no global) that Toon puts inline right behind the feedback widget's tag
 * (src/feedback-widget/place.ts). Inline, so a page pays no request for it.
 * It lands next to the SPA in dist/, after the SPA build, which empties
 * dist/ first.
 */
import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';

export default defineConfig({
  publicDir: false,
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    copyPublicDir: false,
    target: 'es2020',
    sourcemap: false,
    minify: true,
    lib: {
      entry: fileURLToPath(new URL('./src/feedback-place/main.ts', import.meta.url)),
      formats: ['iife'],
      name: 'toonFeedbackPlace',
      fileName: () => 'feedback-place.js',
    },
  },
});
