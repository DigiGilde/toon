/**
 * The place script's entry: a classic script Toon puts inline right behind
 * the widget's tag, so it runs before the page's own scripts
 * (feedback-widget/place.ts).
 */
import { restorePlace } from '../feedback-widget/place';

restorePlace(document.currentScript, location, (url) => {
  try {
    history.replaceState(history.state, '', url);
  } catch {
    // An opaque origin may refuse; the widget then finds the link as before.
  }
});
