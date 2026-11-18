/**
 * The feedback widget: one minified classic script (IIFE, no exports, so no
 * global) that Toon adds to the pages of sites with feedback on. It lands next
 * to the SPA in dist/, after the SPA build, which empties dist/ first.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';

const htmlToImageLicense = readFileSync(
  fileURLToPath(new URL('./node_modules/html-to-image/LICENSE', import.meta.url)),
  'utf8',
)
  .replace(/^MIT License\s*/, '')
  .trim();

if (htmlToImageLicense.includes('*/')) {
  throw new Error('The html-to-image licence would end the legal comment early.');
}

/** Added after minification, so it is always the first thing in the file. */
export const LEGAL_COMMENT =
  '/*! Toon feedback widget. EUPL-1.2. https://github.com/DigiGilde/toon. ' +
  `Includes html-to-image (MIT), ${htmlToImageLicense} */`;

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
      entry: fileURLToPath(new URL('./src/feedback-widget/main.ts', import.meta.url)),
      formats: ['iife'],
      name: 'toonFeedback',
      fileName: () => 'feedback-widget.js',
    },
    rolldownOptions: {
      output: { postBanner: LEGAL_COMMENT },
    },
  },
});
