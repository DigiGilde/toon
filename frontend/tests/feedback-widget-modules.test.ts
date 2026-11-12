/**
 * The feedback widget's source, held to the rules of its design (spec §16.4).
 *
 * The import guard comes first. Coverage only measures files a test loads, so
 * a widget module no test imports would be missing from the report while the
 * run stays green. Loading every module here puts each one in the report,
 * where the 100% floor holds it.
 *
 * The rest reads the source as text: the widget runs inside other people's
 * pages, so it may not use what would touch them (storage, cookies, custom
 * elements, markup from strings, which only the screenshot parses, to read
 * back the SVG of its own copy), may not pull in the SPA's Vue or design
 * system, keeps its only coverage exclusion in the platform adapter, and uses
 * every string of its catalogue.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { widgetNl } from '../src/i18n/widget.nl';

const WIDGET = resolve(dirname(fileURLToPath(import.meta.url)), '../src/feedback-widget');

const modules = import.meta.glob(['../src/feedback-widget/**/*.ts', '!../src/feedback-widget/**/*.test.ts']);

function sources(directory = WIDGET): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) return sources(full);
    return entry.endsWith('.ts') && !entry.endsWith('.test.ts') ? [full] : [];
  });
}

/** The source without comments, so prose about a rule is no finding. */
function code(file: string): string {
  return readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

describe('the feedback widget modules', () => {
  it('are all imported, so none escapes the coverage report', async () => {
    const onDisk = sources().map((file) => relative(WIDGET, file)).sort();
    const globbed = Object.keys(modules)
      .map((path) => path.replace('../src/feedback-widget/', ''))
      .sort();
    expect(globbed).toEqual(onDisk);
    expect(globbed.length).toBeGreaterThan(15);
    for (const load of Object.values(modules)) await load();
  });

  it.each([
    ['innerHTML', /\binnerHTML\b/],
    ['outerHTML', /\bouterHTML\s*=/],
    ['insertAdjacentHTML', /\binsertAdjacentHTML\b/],
    ['document.write', /\bdocument\s*\.\s*write/],
    ['localStorage', /\blocalStorage\b/],
    ['sessionStorage', /\bsessionStorage\b/],
    ['indexedDB', /\bindexedDB\b/],
    ['document.cookie', /\bdocument\s*\.\s*cookie\b/],
    ['customElements', /\bcustomElements\b/],
    ['eval', /\beval\s*\(/],
    ['new Function', /\bnew\s+Function\s*\(/],
    ['a global of its own', /\b(?:window|globalThis)\s*\.\s*\w+\s*=[^=]/],
  ])('never use %s', (_name, pattern) => {
    const offending = sources().filter((file) => pattern.test(code(file)));
    expect(offending.map((file) => relative(WIDGET, file))).toEqual([]);
  });

  it('parse markup only to read back the SVG of the screenshot, never to build what they show', () => {
    const parsing = sources().filter((file) => /\bDOMParser\b/.test(code(file)));
    expect(parsing.map((file) => relative(WIDGET, file))).toEqual(['screenshot.ts']);
  });

  it('never import Vue, the design system or the SPA language module', () => {
    const forbidden = /from\s+['"](?:vue|vue-router|@nldd\/[^'"]*|@\/[^'"]*|\.\.\/i18n(?:\/index)?|\.\.\/i18n\/(?:nl|en))['"]/;
    const offending = sources().filter((file) => forbidden.test(code(file)));
    expect(offending.map((file) => relative(WIDGET, file))).toEqual([]);
  });

  it('exclude code from coverage in the platform adapter only, with a reason', () => {
    const marked = sources().filter((file) => /v8 ignore/.test(readFileSync(file, 'utf8')));
    expect(marked.map((file) => relative(WIDGET, file))).toEqual(['platform.ts']);
    const platform = readFileSync(join(WIDGET, 'platform.ts'), 'utf8');
    expect(platform).toMatch(/\/\* v8 ignore start -- \S.+\*\//);
    expect(platform).toContain('/* v8 ignore stop */');
  });

  it('use every string of the widget catalogue', () => {
    const all = sources()
      .map((file) => readFileSync(file, 'utf8'))
      .join('\n');
    const unused = Object.keys(widgetNl).filter((key) => !all.includes(`'${key}'`));
    expect(unused).toEqual([]);
  });
});
