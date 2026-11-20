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
const PLACE = resolve(FRONTEND, 'dist/feedback-place.js');

function built(path: string): string {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    throw new Error(`${path} not found. Run 'npm run build' first.`);
  }
}

function bundle(): string {
  return built(BUNDLE);
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

/**
 * The place script, which Toon puts inline right behind the widget's tag in
 * every page that gets the widget (serving/feedback_widget.py refuses one
 * that cannot stand inside a script element).
 */
describe('the built place script', () => {
  it('is one small classic script that defines no global', () => {
    const code = built(PLACE).trim();
    expect(code.startsWith('(function(){')).toBe(true);
    expect(code.endsWith('})();')).toBe(true);
    expect(code.length).toBeLessThan(1500);
  });

  it('can stand inline in a script element, and touches nothing it may not', () => {
    const code = built(PLACE);
    expect(/^[\x20-\x7e\n]*$/.test(code)).toBe(true);
    expect(code).not.toMatch(/<\/script|<script|<!--/i);
    for (const forbidden of ['localStorage', 'sessionStorage', 'document.cookie', 'sourceMappingURL']) {
      expect(code.includes(forbidden), forbidden).toBe(false);
    }
  });
});
