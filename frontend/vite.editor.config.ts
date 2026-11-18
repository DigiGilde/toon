/**
 * The feedback editor: the script of the frame the feedback widget writes in
 * (src/feedback-editor/). One minified classic script (IIFE, every dynamic
 * import inlined, no exports and so no global), because the frame's origin
 * is opaque and a module script, fetched in CORS mode, would not load. It
 * lands next to the SPA in dist/, after the SPA build, which empties dist/
 * first.
 *
 * Its legal comment names every package in it with its licence, and carries
 * each licence text once, read from the packages themselves.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vite';

/** The package directory a bundled module comes from, or null for our own code. */
function packageOf(moduleId: string): string | null {
  const at = moduleId.lastIndexOf('/node_modules/');
  if (at < 0) return null;
  const rest = moduleId.slice(at + '/node_modules/'.length).split('/');
  const name = rest[0]!.startsWith('@') ? `${rest[0]}/${rest[1]}` : rest[0]!;
  return join(moduleId.slice(0, at), 'node_modules', name);
}

function licenceText(directory: string): string | null {
  const file = readdirSync(directory).find((name) => /^(licen[cs]e|copying)(\.(md|txt))?$/i.test(name));
  return file === undefined ? null : readFileSync(join(directory, file), 'utf8').trim();
}

/** The legal comment for the packages in the bundle. */
export function legalComment(moduleIds: readonly string[]): string {
  const directories = [...new Set(moduleIds.map(packageOf).filter((dir): dir is string => dir !== null))].sort();
  const texts = new Map<string, string[]>();
  const names: string[] = [];
  for (const directory of directories) {
    const manifest = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8')) as {
      name: string;
      version: string;
      license: string;
    };
    names.push(`${manifest.name} ${manifest.version} (${manifest.license})`);
    // The EUPL is Toon's own licence, named above; the permissive ones ask
    // for their text to travel with the code.
    if (manifest.license.startsWith('EUPL')) continue;
    const text = licenceText(directory);
    if (text === null) throw new Error(`No licence text in ${directory}.`);
    texts.set(text, [...(texts.get(text) ?? []), manifest.name]);
  }
  const comment =
    '/*! Toon feedback editor. EUPL-1.2. https://github.com/DigiGilde/toon.\n' +
    `Includes ${names.join(', ')}.\n` +
    [...texts].map(([text, owners]) => `\n${owners.join(', ')}:\n${text}\n`).join('') +
    '*/';
  if (comment.slice(3, -2).includes('*/')) throw new Error('A licence would end the legal comment early.');
  return comment;
}

export default defineConfig({
  publicDir: false,
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    copyPublicDir: false,
    target: 'es2022',
    sourcemap: false,
    minify: true,
    lib: {
      entry: fileURLToPath(new URL('./src/feedback-editor/main.ts', import.meta.url)),
      formats: ['iife'],
      name: 'toonFeedbackEditor',
      fileName: () => 'feedback-editor.js',
    },
    rolldownOptions: {
      output: { postBanner: (chunk) => legalComment(chunk.moduleIds) },
    },
  },
});
