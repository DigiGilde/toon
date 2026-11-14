/**
 * Every heading sits where the design system breaks a long word in it.
 *
 * Toon has no hyphenation rule of its own for headings: nldd-title and
 * nldd-rich-text both hyphenate theirs (`src/global.test.ts` holds the latter
 * to it). A bare `<h2>` anywhere else lets one long word ("Bewaartermijn" at
 * 200% text) push a 320px screen into a horizontal scroll (WCAG 1.4.10).
 *
 * So this reads every template and asks of each heading whether an
 * nldd-title, an nldd-rich-text, or a component of ours whose default slot
 * sits in one of those is around it. A heading that the Markdown renderer
 * makes counts as a heading wherever its component is placed.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../src');

const WRAPPING = new Set(['nldd-title', 'nldd-rich-text']);
const VOID = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track',
  'wbr',
]);
// A start or end tag, with quoted attribute values that may hold a `>`.
const TAG =
  /<(\/?)([A-Za-z][\w.-]*)((?:\s+[^\s"'>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)\s*(\/?)>/g;

interface Found {
  file: string;
  line: number;
  tag: string;
}

function vueFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return vueFiles(path);
    return name.endsWith('.vue') ? [path] : [];
  });
}

/** The outer `<template>` of a single-file component, comments blanked. */
function template(source: string): string {
  const start = source.indexOf('<template>');
  const end = source.lastIndexOf('</template>');
  if (start < 0 || end < 0) return '';
  const body = source.slice(0, end);
  // Keep the offsets, so a line number still points at the source.
  return body
    .replace(/<!--[\s\S]*?-->/g, (comment) => comment.replace(/[^\n]/g, ' '))
    .replace(/^[\s\S]*?<template>/, (head) => head.replace(/[^\n]/g, ' '));
}

function isHeading(tag: string, attrs: string, headingComponents: Set<string>): boolean {
  if (/^h[1-6]$/.test(tag)) return true;
  if (tag === 'component') return /:is="`h\$\{|:is="'h[1-6]'/.test(attrs);
  return headingComponents.has(tag);
}

/**
 * Walks a template with a stack of open elements and reports every heading,
 * and every default `<slot>`, with whether a wrapping element is open around it.
 */
function walk(
  source: string,
  wrapping: Set<string>,
  headingComponents: Set<string> = new Set(),
): { headings: (Found & { wrapped: boolean })[]; slots: boolean[] } {
  const text = template(source);
  const stack: string[] = [];
  const headings: (Found & { wrapped: boolean })[] = [];
  const slots: boolean[] = [];
  for (const match of text.matchAll(TAG)) {
    const [, closing, tag, attrs, selfClosing] = match as unknown as [string, string, string, string, string];
    if (closing) {
      const at = stack.lastIndexOf(tag);
      if (at >= 0) stack.length = at;
      continue;
    }
    const wrapped = stack.some((open) => wrapping.has(open));
    if (isHeading(tag, attrs, headingComponents)) {
      const line = text.slice(0, match.index).split('\n').length;
      headings.push({ file: '', line, tag, wrapped });
    }
    if (tag === 'slot' && !/\sname=/.test(attrs)) slots.push(wrapped);
    if (!selfClosing && !VOID.has(tag)) stack.push(tag);
  }
  return { headings, slots };
}

/** Local components that render Markdown, whose headings come from markdown.ts. */
function markdownComponents(source: string): Set<string> {
  const names = [...source.matchAll(/const (\w+) = [^\n]*renderMarkdown\(/g)].map((m) => m[1]!);
  return new Set(names);
}

const files = vueFiles(SRC).map((path) => ({ path, source: readFileSync(path, 'utf8') }));

/** Our components whose default slot lands in nldd-title or nldd-rich-text. */
const wrappers = new Set(
  files
    .filter(({ source }) => {
      const { slots } = walk(source, WRAPPING);
      return slots.length > 0 && slots.every(Boolean);
    })
    .map(({ path }) => basename(path, '.vue')),
);
const allowed = new Set([...WRAPPING, ...wrappers]);

describe('Headings', () => {
  it('finds a bare heading, and accepts one in a wrapping component', () => {
    const bare = walk('<template><section><h2>Bewaartermijn</h2></section></template>', allowed);
    expect(bare.headings.map((h) => h.wrapped)).toEqual([false]);
    const held = walk(
      '<template><nldd-rich-text><p a="x > y"></p><h2>Bewaartermijn</h2></nldd-rich-text></template>',
      allowed,
    );
    expect(held.headings.map((h) => h.wrapped)).toEqual([true]);
    const after = walk(
      '<template><nldd-title><h1>A</h1></nldd-title><br><h2>B</h2></template>',
      allowed,
    );
    expect(after.headings.map((h) => h.wrapped)).toEqual([true, false]);
  });

  it('knows the page wrapper whose slot sits in nldd-rich-text', () => {
    expect(wrappers).toContain('PlatformPage');
  });

  it('puts every heading in nldd-title or nldd-rich-text', () => {
    const bare: Found[] = [];
    let seen = 0;
    for (const { path, source } of files) {
      const { headings } = walk(source, allowed, markdownComponents(source));
      seen += headings.length;
      for (const heading of headings) {
        if (!heading.wrapped) {
          bare.push({ file: path.slice(SRC.length + 1), line: heading.line, tag: heading.tag });
        }
      }
    }
    // A parser that finds nothing would pass this test for the wrong reason.
    expect(seen).toBeGreaterThan(20);
    expect(bare).toEqual([]);
  });
});
