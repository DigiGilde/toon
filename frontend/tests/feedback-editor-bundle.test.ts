/**
 * The built feedback editor, the script of the widget's editor frame
 * (src/feedback-editor/): one classic script, because the frame's origin is
 * opaque and a module script would not load there, that starts with its
 * legal comment. The comment names every bundled package with its licence
 * and carries the text of each permissive licence, which those licences
 * require in every copy. Run it after `npm run build`; without the file the
 * test fails with a clear message rather than passing silently.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUNDLE = resolve(FRONTEND, 'dist/feedback-editor.js');

function bundle(): string {
  try {
    return readFileSync(BUNDLE, 'utf8');
  } catch {
    throw new Error(`dist/feedback-editor.js not found at ${BUNDLE}. Run 'npm run build' first.`);
  }
}

function legalComment(): string {
  const code = bundle();
  return code.slice(0, code.indexOf('*/') + 2);
}

function licence(pkg: string): string {
  return readFileSync(resolve(FRONTEND, 'node_modules', pkg, 'LICENSE'), 'utf8').trim();
}

/** The installed version, so a dependency bump does not need this test changed. */
function version(pkg: string): string {
  return (JSON.parse(readFileSync(resolve(FRONTEND, 'node_modules', pkg, 'package.json'), 'utf8')) as { version: string })
    .version;
}

describe('the built feedback editor', () => {
  it('starts with the legal comment, naming Toon and every package in it with its licence', () => {
    const comment = legalComment();
    expect(comment.startsWith('/*! Toon feedback editor. EUPL-1.2. https://github.com/DigiGilde/toon.\nIncludes ')).toBe(
      true,
    );
    for (const named of [
      `@nldd/design-system ${version('@nldd/design-system')} (EUPL-1.2)`,
      '@codemirror/state',
      '@codemirror/view',
      '@lezer/markdown',
      'lit-html',
      'style-mod',
    ]) {
      expect(comment, named).toContain(named);
    }
  });

  it('carries the text of each permissive licence', () => {
    const comment = legalComment();
    expect(comment).toContain(licence('@codemirror/view'));
    expect(comment).toContain(licence('lit-html'));
    expect(comment).toContain('Permission is hereby granted, free of charge');
    expect(comment).toContain('Redistribution and use in source and binary forms');
  });

  it('is one classic script that defines no global and no module syntax', () => {
    const code = bundle();
    const body = code.slice(code.indexOf('*/') + 2).trim();
    expect(body.startsWith('(function(){')).toBe(true);
    expect(body.endsWith('})();')).toBe(true);
    expect(body).not.toMatch(/^\s*(?:import|export)\s/m);
    expect(body).not.toMatch(/^var \w+=/);
    expect(body).not.toMatch(/\bimport\(/);
  });

  it('has the NLDD tokens, and no sourcemap reference and no web font', () => {
    const code = bundle();
    expect(code).toContain('--semantics-input-fields-border-color');
    expect(code).not.toMatch(/sourceMappingURL/);
    expect(code).not.toMatch(/\.woff2?\b/);
    expect(code).not.toContain('@font-face{');
  });
});
