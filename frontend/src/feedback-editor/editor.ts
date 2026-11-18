/**
 * nldd-text-editor with what the feedback field needs that it does not
 * offer: a maximum length, select all, the caret at the end, and whether a
 * list of its own shows.
 * Registered under a name of its own; importing the base class defines
 * nldd-text-editor as well, in the frame's own registry.
 */
import { EditorSelection, type Extension } from '@codemirror/state';
import { NLDDTextEditor } from '@nldd/design-system/text-editor';

import type { FrameEditor } from './frame';
import { lengthLimit } from './limit';

export const EDITOR_TAG = 'toon-feedback-editor';

export class FeedbackEditor extends NLDDTextEditor implements FrameEditor {
  maxLength = Number.MAX_SAFE_INTEGER;

  protected override buildExtensions(): Extension[] {
    return [...super.buildExtensions(), lengthLimit(() => this.maxLength)];
  }

  selectAll(): void {
    const view = this.view;
    if (view === undefined) return;
    view.dispatch({ selection: EditorSelection.single(0, view.state.doc.length) });
    view.focus();
  }

  caretToEnd(): void {
    const view = this.view;
    if (view === undefined) return;
    view.dispatch({ selection: EditorSelection.cursor(view.state.doc.length) });
  }

  showsPopup(): boolean {
    return this.shadowRoot?.querySelector('.cm-tooltip') != null;
  }
}

export function defineEditor(registry: CustomElementRegistry): void {
  if (registry.get(EDITOR_TAG) === undefined) registry.define(EDITOR_TAG, FeedbackEditor);
}
