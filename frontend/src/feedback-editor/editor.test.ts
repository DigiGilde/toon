import { afterEach, describe, expect, it, onTestFinished } from 'vitest';

import { defineEditor, EDITOR_TAG, FeedbackEditor } from './editor';

defineEditor(customElements);

afterEach(() => {
  document.body.innerHTML = '';
});

/** The editor's CodeMirror view, which the element keeps to itself. */
function view(editor: FeedbackEditor) {
  return (editor as unknown as { view: import('@codemirror/view').EditorView }).view;
}

async function mounted(value: string, maxLength = 10): Promise<FeedbackEditor> {
  const editor = document.createElement(EDITOR_TAG) as FeedbackEditor;
  editor.maxLength = maxLength;
  editor.value = value;
  document.body.append(editor);
  await editor.updateComplete;
  return editor;
}

describe('the feedback editor', () => {
  it('is defined once, next to nldd-text-editor', () => {
    defineEditor(customElements);
    expect(customElements.get(EDITOR_TAG)).toBe(FeedbackEditor);
    expect(customElements.get('nldd-text-editor')).toBeDefined();
  });

  it('holds the text to its maximum length', async () => {
    const editor = await mounted('abcdefgh');
    view(editor).dispatch({ changes: { from: 8, insert: 'ijkl' } });
    expect(editor.value).toBe('abcdefghij');
  });

  it('puts the caret at the end, and selects all', async () => {
    // CodeMirror reads a selection in a shadow root through execCommand
    // where the root has no getSelection, as in jsdom.
    const document_ = document as unknown as { execCommand?: () => boolean };
    document_.execCommand = () => false;
    onTestFinished(() => {
      delete document_.execCommand;
    });
    const editor = await mounted('abc');
    editor.caretToEnd();
    expect(view(editor).state.selection.main.head).toBe(3);
    editor.selectAll();
    const { from, to } = view(editor).state.selection.main;
    expect([from, to]).toEqual([0, 3]);
  });

  it('says whether a list or message of its own shows', async () => {
    const editor = await mounted('abc');
    expect(editor.showsPopup()).toBe(false);
    const tooltip = document.createElement('div');
    tooltip.className = 'cm-tooltip';
    editor.shadowRoot!.append(tooltip);
    expect(editor.showsPopup()).toBe(true);
  });

  it('does nothing before its view exists', () => {
    const editor = document.createElement(EDITOR_TAG) as FeedbackEditor;
    editor.caretToEnd();
    editor.selectAll();
    expect(editor.value).toBe('');
  });
});
