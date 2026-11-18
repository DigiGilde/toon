/**
 * The built feedback widget, as Toon serves it on the content host: one
 * classic script that starts with its preserved legal comment (EUPL-1.2 for
 * Toon, and html-to-image's MIT copyright and permission notice, which the MIT
 * licence requires in every copy). Run it after `npm run build`; without the
 * file the test fails with a clear message rather than passing silently.
 * build-output.test.ts already holds every file in dist/ to no sourcemaps and
 * no eval or new Function.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUNDLE = resolve(FRONTEND, 'dist/feedback-widget.js');

function bundle(): string {
  try {
    return readFileSync(BUNDLE, 'utf8');
  } catch {
    throw new Error(`dist/feedback-widget.js not found at ${BUNDLE}. Run 'npm run build' first.`);
  }
}

/** The licence as the package ships it, without its title line. */
function htmlToImageNotice(): string {
  return readFileSync(resolve(FRONTEND, 'node_modules/html-to-image/LICENSE'), 'utf8')
    .replace(/^MIT License\s*/, '')
    .trim();
}

describe('the built feedback widget', () => {
  it('starts with the legal comment: Toon under EUPL-1.2, and the html-to-image notice', () => {
    const code = bundle();
    const notice = htmlToImageNotice();
    expect(notice).toMatch(/^Copyright \(c\) .+ W\.Y\./);
    expect(notice).toContain('Permission is hereby granted, free of charge');
    expect(
      code.startsWith(
        `/*! Toon feedback widget. EUPL-1.2. https://github.com/DigiGilde/toon. Includes html-to-image (MIT), ${notice} */`,
      ),
    ).toBe(true);
  });

  it('is one classic script that defines no global and no module syntax', () => {
    const code = bundle();
    const body = code.slice(code.indexOf('*/') + 2).trim();
    expect(body.startsWith('(function(){')).toBe(true);
    expect(body.endsWith('})();')).toBe(true);
    expect(body).not.toMatch(/^\s*(?:import|export)\s/m);
    expect(body).not.toMatch(/^var \w+=/);
  });

  it('carries no Vue and none of what the widget may not touch', () => {
    const code = bundle();
    for (const forbidden of ['localStorage', 'sessionStorage', 'document.cookie', 'customElements', '__VUE']) {
      expect(code.includes(forbidden), forbidden).toBe(false);
    }
    expect(code.length).toBeLessThan(150_000);
  });

  it('has no sourcemap reference', () => {
    expect(bundle()).not.toMatch(/sourceMappingURL/);
  });
});
