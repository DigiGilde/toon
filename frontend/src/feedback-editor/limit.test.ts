import { EditorSelection, EditorState, type TransactionSpec } from '@codemirror/state';
import { describe, expect, it } from 'vitest';

import { lengthLimit } from './limit';

function state(doc: string, max: number, cursor = doc.length): EditorState {
  return EditorState.create({ doc, selection: EditorSelection.cursor(cursor), extensions: [lengthLimit(() => max)] });
}

function apply(start: EditorState, spec: TransactionSpec): EditorState {
  return start.update(spec).state;
}

describe('the length limit', () => {
  it('lets typing through up to the limit', () => {
    const after = apply(state('abc', 5), { changes: { from: 3, insert: 'de' }, userEvent: 'input.type' });
    expect(after.doc.toString()).toBe('abcde');
  });

  it('stops typing at the limit', () => {
    const after = apply(state('abcde', 5), { changes: { from: 5, insert: 'f' }, userEvent: 'input.type' });
    expect(after.doc.toString()).toBe('abcde');
    expect(after.selection.main.head).toBe(5);
  });

  it('cuts a paste to what fits, with the caret after it', () => {
    const start = state('ab', 5, 1);
    const tr = start.update({ changes: { from: 1, insert: '12345' }, userEvent: 'input.paste' });
    expect(tr.state.doc.toString()).toBe('a123b');
    expect(tr.state.selection.main.head).toBe(4);
    expect(tr.isUserEvent('input.paste')).toBe(true);
  });

  it('cuts a paste that replaces a selection', () => {
    const start = EditorState.create({
      doc: 'abcd',
      selection: EditorSelection.single(1, 3),
      extensions: [lengthLimit(() => 5)],
    });
    expect(apply(start, { changes: { from: 1, to: 3, insert: 'WXYZ' } }).doc.toString()).toBe('aWXYd');
  });

  it('never splits an emoji in two', () => {
    const after = apply(state('abc', 5), { changes: { from: 3, insert: 'x\u{1F600}' } });
    expect(after.doc.toString()).toBe('abcx');
  });

  it('cuts the last of several insertions first', () => {
    const after = apply(state('abc', 6), {
      changes: [
        { from: 0, insert: '12' },
        { from: 3, insert: '34' },
      ],
    });
    expect(after.doc.toString()).toBe('12abc3');
  });

  it('takes from earlier insertions when the last one is not enough', () => {
    const after = apply(state('abc', 4), {
      changes: [
        { from: 0, insert: '12' },
        { from: 3, insert: '3' },
      ],
    });
    expect(after.doc.toString()).toBe('1abc');
  });

  it('lets through an edit that leaves a text over the limit no longer', () => {
    const start = EditorState.create({ doc: 'abcdefg', extensions: [lengthLimit(() => 5)] });
    expect(apply(start, { changes: { from: 0, to: 2, insert: 'X' } }).doc.toString()).toBe('Xcdefg');
    expect(apply(start, { changes: { from: 0, to: 1, insert: 'X' } }).doc.toString()).toBe('Xbcdefg');
  });

  it('leaves a transaction without changes alone', () => {
    const after = apply(state('abcdefg', 5), { selection: EditorSelection.cursor(1) });
    expect(after.selection.main.head).toBe(1);
  });

  it('reads the limit each time', () => {
    let max = 3;
    const start = EditorState.create({ doc: 'abc', extensions: [lengthLimit(() => max)] });
    expect(apply(start, { changes: { from: 3, insert: 'd' } }).doc.toString()).toBe('abc');
    max = 4;
    expect(apply(start, { changes: { from: 3, insert: 'd' } }).doc.toString()).toBe('abcd');
  });
});
