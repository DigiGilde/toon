/**
 * What the feedback widget and its editor frame say to each other. The frame
 * says hello to its parent once, with postMessage to any origin: its own
 * origin is opaque and the parent's may be too. The widget answers only a
 * hello from the window of its own iframe, by handing over one end of a
 * MessageChannel, and everything after goes over that port alone, so no
 * other window on the page can join in.
 */

export const HELLO = 'toon-feedback-editor:hello';
export const PORT = 'toon-feedback-editor:port';

export type Scheme = 'light' | 'dark';

/** Room around the editor in the frame for its focus ring, which it draws
 * outside its box: the widget gives the frame as much more room. */
export const FRAME_PADDING = 4;

/** Everything the frame needs to show the field, sent once over the port. */
export interface Init {
  type: 'init';
  value: string;
  /** The field's label, which cannot point into the frame. */
  label: string;
  lang: 'nl' | 'en';
  maxLength: number;
  rows: number;
  scheme: Scheme;
  invalid: boolean;
  /** The field's message, which aria-describedby cannot reach in the frame. */
  description: string;
}

export type ToFrame =
  | Init
  /** The text changed outside the editor, such as a reply that was sent. */
  | { type: 'value'; value: string }
  | { type: 'state'; invalid: boolean; description: string }
  | { type: 'scheme'; scheme: Scheme }
  | { type: 'focus' }
  /** Select all the text, for someone to copy it by hand. */
  | { type: 'select' };

export type FromFrame =
  /** The editor shows the text of the init. */
  | { type: 'ready'; height: number }
  | { type: 'input'; value: string }
  | { type: 'height'; height: number }
  /** Escape while no list of the editor shows, for the panel. */
  | { type: 'escape' }
  /** Something went wrong in the frame: the widget goes back to its text field. */
  | { type: 'failed' };
