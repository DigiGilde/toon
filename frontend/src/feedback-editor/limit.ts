/**
 * What `maxlength` does for a textarea, for the CodeMirror editor: counted in
 * UTF-16 units like the field and the widget's own check, typing stops at
 * the limit, a paste is cut to what fits, and an edit that leaves the text
 * no longer than before always goes through.
 */
import { EditorState, type Extension, Transaction, type TransactionSpec } from '@codemirror/state';

interface Change {
  from: number;
  to: number;
  insert: string;
}

/** Cut `excess` units off the end of `text`, never between the two halves
 * of a surrogate pair. */
function shorten(text: string, excess: number): string {
  let end = Math.max(0, text.length - excess);
  if (end > 0 && end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1]!)) end -= 1;
  return text.slice(0, end);
}

export function limitTransaction(tr: Transaction, max: number): Transaction | TransactionSpec {
  const before = tr.startState.doc.length;
  const after = tr.newDoc.length;
  if (!tr.docChanged || after <= max || after <= before) return tr;
  const changes: Change[] = [];
  tr.changes.iterChanges((from, to, _fromB, _toB, inserted) => {
    changes.push({ from, to, insert: inserted.toString() });
  });
  // The last insertion gives way first, as a paste is cut at its end.
  let excess = after - max;
  for (let index = changes.length - 1; index >= 0 && excess > 0; index--) {
    const change = changes[index]!;
    const kept = shorten(change.insert, excess);
    excess -= change.insert.length - kept.length;
    change.insert = kept;
  }
  const set = tr.startState.changes(changes);
  return {
    changes: set,
    selection: tr.startState.selection.map(set, 1),
    effects: tr.effects,
    scrollIntoView: tr.scrollIntoView,
    userEvent: tr.annotation(Transaction.userEvent),
  };
}

export function lengthLimit(max: () => number): Extension {
  return EditorState.transactionFilter.of((tr) => limitTransaction(tr, max()));
}
