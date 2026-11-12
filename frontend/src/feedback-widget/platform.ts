/**
 * The browser calls the rest of the widget cannot make in jsdom: layout
 * (elementsFromPoint, client rects, the visual viewport), canvas drawing and
 * encoding, html-to-image, the clipboard, form submission and history. Thin on
 * purpose: every decision is taken in the measured modules, and the
 * real-browser check runs each function here in Chromium.
 */
import { toSvg } from 'html-to-image';

import { type ClipPart, clipping, clipThrough, windowLeft } from './rect';
import {
  captureScale,
  cssUrls,
  IMAGE_REQUEST,
  imageWillFail,
  markEdges,
  overflowToShow,
  pageBackground,
  PLACEHOLDER_IMAGE,
  withVisibleOverflow,
} from './screenshot';
import type { Box, DrawPlan, Mark, Platform, Point, Scroller } from './types';

/* v8 ignore start -- browser-only: jsdom has no layout, canvas, clipboard or visualViewport, so these run in a real browser instead */
const ACCENT = '#184576';
const DARK = '#1b1f24';

function toBox(rect: DOMRectReadOnly): Box {
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

/** Whether the capture will draw this element's image as a grey area. */
function readsBlockedImage(node: Element, base: string, opaque: boolean): boolean {
  if (node instanceof HTMLImageElement) {
    const source = node.currentSrc || node.src;
    return source !== '' && imageWillFail(source, base, opaque);
  }
  if (node instanceof SVGImageElement) {
    const source = node.href.baseVal;
    return source !== '' && imageWillFail(source, base, opaque);
  }
  const background = getComputedStyle(node).backgroundImage;
  return background !== 'none' && cssUrls(background).some((url) => imageWillFail(url, base, opaque));
}

/** The element whose box holds a node as the page is drawn: the slot it is
 * assigned to, its parent, or the host of the shadow root it is in. */
function layoutParent(node: Node): Element | null {
  const slot = node instanceof Element || node instanceof Text ? node.assignedSlot : null;
  if (slot !== null) return slot;
  if (node.parentElement !== null) return node.parentElement;
  const parent = node.parentNode;
  return parent instanceof ShadowRoot ? parent.host : null;
}

/** Whether the visitor can scroll an axis with this overflow. Hidden and
 * clip can be scrolled by a script only, and the visitor could not scroll
 * them back. */
function scrolls(overflow: string): boolean {
  return overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay';
}

/** Whether the body's overflow is the window's: the root's is visible. */
function bodyIsWindow(): boolean {
  const root = getComputedStyle(document.documentElement);
  return root.overflowX === 'visible' && root.overflowY === 'visible';
}

/** An element's scroll range on each axis. Right to left, and in a vertical
 * writing mode whose lines run from the right, scrolling to the left counts
 * down from 0. */
function scrollRange(element: Element, style: CSSStyleDeclaration): { min: Point; max: Point } {
  const vertical = style.writingMode.startsWith('vertical') || style.writingMode.startsWith('sideways');
  const rtl = style.direction === 'rtl';
  const reversedX = vertical ? style.writingMode.endsWith('-rl') : rtl;
  const reversedY = vertical && rtl !== (style.writingMode === 'sideways-lr');
  const width = element.scrollWidth - element.clientWidth;
  const height = element.scrollHeight - element.clientHeight;
  const range = (size: number, reversed: boolean, at: number, can: boolean) =>
    !can || size <= 0 ? [at, at] : reversed ? [-size, 0] : [0, size];
  const [minX, maxX] = range(width, reversedX, element.scrollLeft, scrolls(style.overflowX));
  const [minY, maxY] = range(height, reversedY, element.scrollTop, scrolls(style.overflowY));
  return { min: { x: minX!, y: minY! }, max: { x: maxX!, y: maxY! } };
}

/** An image ready to draw, loaded as html-to-image loads the SVG of its
 * copy: decoded, and drawn a frame later. */
function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      image.decode().then(() => requestAnimationFrame(() => resolve(image)), reject);
    };
    image.onerror = reject;
    image.crossOrigin = 'anonymous';
    image.decoding = 'async';
    image.src = url;
  });
}

function canvas(width: number, height: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const element = document.createElement('canvas');
  element.width = width;
  element.height = height;
  return [element, element.getContext('2d')!];
}

function drawMark(context: CanvasRenderingContext2D, mark: Mark, scale: number): void {
  if (mark.type === 'pin') {
    const x = mark.x * scale;
    const y = mark.y * scale;
    const radius = 12 * scale;
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = ACCENT;
    context.fill();
    context.lineWidth = 3 * scale;
    context.strokeStyle = '#ffffff';
    context.stroke();
    context.beginPath();
    context.arc(x, y, radius + 2.5 * scale, 0, Math.PI * 2);
    context.lineWidth = 2 * scale;
    context.strokeStyle = DARK;
    context.stroke();
    return;
  }
  // As the mark on the page: two tones around the text, whatever the page's colours.
  context.fillStyle = 'rgba(255, 213, 0, 0.35)';
  for (const rect of mark.rects) {
    context.fillRect(rect.x * scale, rect.y * scale, rect.width * scale, rect.height * scale);
    for (const edge of markEdges(rect, scale)) {
      context.lineWidth = edge.width;
      context.strokeStyle = edge.color;
      context.strokeRect(edge.rect.x, edge.rect.y, edge.rect.width, edge.rect.height);
    }
  }
}

function draw(source: HTMLCanvasElement, scale: number, plan: DrawPlan): HTMLCanvasElement {
  const [work, context] = canvas(source.width, source.height);
  context.drawImage(source, 0, 0);
  for (const mark of plan.marks) drawMark(context, mark, scale);
  context.fillStyle = DARK;
  for (const cover of plan.covers) {
    context.fillRect(cover.x * scale, cover.y * scale, cover.width * scale, cover.height * scale);
  }
  const [result, output] = canvas(plan.target.width, plan.target.height);
  output.imageSmoothingQuality = 'high';
  output.drawImage(
    work,
    plan.source.x * scale,
    plan.source.y * scale,
    plan.source.width * scale,
    plan.source.height * scale,
    0,
    0,
    result.width,
    result.height,
  );
  return result;
}

export function createPlatform(): Platform {
  return {
    currentScript: () => (document.currentScript instanceof HTMLScriptElement ? document.currentScript : null),
    elementAt: (x, y, host) =>
      document.elementsFromPoint(x, y).find((element) => element !== host && !host.contains(element)) ?? null,
    box: (element) => toBox(element.getBoundingClientRect()),
    keyboardFocus(element) {
      try {
        return element.matches(':focus-visible');
      } catch {
        // A browser without :focus-visible: every focus counts, as before.
        return true;
      }
    },
    visibleText: (element) => (element instanceof HTMLElement ? element.innerText : (element.textContent ?? '')),
    rangeBoxes: (range) => Array.from(range.getClientRects(), toBox),
    viewport() {
      const visual = window.visualViewport;
      const root = document.documentElement;
      return {
        width: window.innerWidth,
        height: window.innerHeight,
        visualHeight: visual?.height ?? window.innerHeight,
        visualOffsetTop: visual?.offsetTop ?? 0,
        dpr: window.devicePixelRatio > 0 ? window.devicePixelRatio : 1,
        scrollX: window.scrollX,
        scrollY: window.scrollY,
        docWidth: Math.max(root.scrollWidth, document.body.scrollWidth),
        docHeight: Math.max(root.scrollHeight, document.body.scrollHeight),
      };
    },
    onViewportChange(listener) {
      const visual = window.visualViewport;
      window.addEventListener('resize', listener);
      visual?.addEventListener('resize', listener);
      visual?.addEventListener('scroll', listener);
      return () => {
        window.removeEventListener('resize', listener);
        visual?.removeEventListener('resize', listener);
        visual?.removeEventListener('scroll', listener);
      };
    },
    observeLayout(listener) {
      let frame = 0;
      const schedule = () => {
        if (frame !== 0) return;
        frame = requestAnimationFrame(() => {
          frame = 0;
          listener();
        });
      };
      // The root's box changes with the layout viewport (a resize, a zoom, a
      // rotation) and with content that loads late. Pinch-zoom and scrolling
      // leave it alone.
      const observer = new ResizeObserver(schedule);
      observer.observe(document.documentElement);
      window.addEventListener('resize', schedule);
      return () => {
        observer.disconnect();
        window.removeEventListener('resize', schedule);
        cancelAnimationFrame(frame);
        frame = 0;
      };
    },
    nextFrame(callback) {
      requestAnimationFrame(() => callback());
    },
    clipBox(element) {
      const root = document.documentElement;
      // The body's overflow is the window's while the root's is visible.
      const windowBody = bodyIsWindow();
      const parts: ClipPart[] = [];
      let node: Element | null = element;
      // A fixed element leaves the boxes of its ancestors.
      while (node !== null && node !== root && getComputedStyle(node).position !== 'fixed') {
        node = layoutParent(node);
        if (node === null || node === root || (node === document.body && windowBody)) continue;
        const clip = clipping(node, getComputedStyle(node));
        if (clip === null) continue;
        const rect = node.getBoundingClientRect();
        const box =
          clip.box === 'border'
            ? toBox(rect)
            : { left: rect.left + node.clientLeft, top: rect.top + node.clientTop, width: node.clientWidth, height: node.clientHeight };
        parts.push({ box, x: clip.x, y: clip.y });
      }
      return clipThrough(parts);
    },
    opaqueOrigin: () => window.origin === 'null',
    scrollToPage(x, y) {
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const root = getComputedStyle(document.documentElement).overflowX;
      const body = getComputedStyle(document.body).overflowX;
      window.scrollTo({
        left: windowLeft(x, window.scrollX, window.innerWidth, root, body),
        top: Math.max(0, y - window.innerHeight / 2),
        behavior: smooth ? 'smooth' : 'auto',
      });
    },
    scrollers(node) {
      const root = document.documentElement;
      const windowBody = bodyIsWindow();
      const found: Scroller[] = [];
      const fixed = (at: Node) => at instanceof Element && getComputedStyle(at).position === 'fixed';
      let current: Node | null = node;
      // A fixed element leaves the boxes of its ancestors.
      while (current !== null && current !== root && !fixed(current)) {
        const parent = layoutParent(current);
        current = parent;
        if (parent === null || parent === root || (parent === document.body && windowBody)) continue;
        const style = getComputedStyle(parent);
        if (style.display === 'contents') continue;
        const { min, max } = scrollRange(parent, style);
        if (min.x === max.x && min.y === max.y) continue;
        const rect = parent.getBoundingClientRect();
        found.push({
          element: parent,
          box: {
            left: rect.left + parent.clientLeft,
            top: rect.top + parent.clientTop,
            width: parent.clientWidth,
            height: parent.clientHeight,
          },
          scroll: { x: parent.scrollLeft, y: parent.scrollTop },
          min,
          max,
        });
      }
      return found;
    },
    scrollElementTo(element, x, y) {
      const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      element.scrollTo({ left: x, top: y, behavior: smooth ? 'smooth' : 'auto' });
    },
    async capture(host) {
      const width = window.innerWidth;
      const height = window.innerHeight;
      const scale = captureScale(width, height, window.devicePixelRatio);
      const opaque = window.origin === 'null';
      const base = location.href;
      const root = getComputedStyle(document.documentElement);
      const body = getComputedStyle(document.body);
      const background = pageBackground(root.backgroundColor, body.backgroundColor);
      let placeholders = false;
      const svg = await toSvg(document.documentElement, {
        width,
        height,
        backgroundColor: background,
        // Margins take the copy up to where the window shows the page. A
        // transform would make the root hold the fixed parts and carry them
        // off with it, and leave the sticky parts where they sit at the top.
        // The copied logical margins go, or they would win over these.
        // flow-root keeps the body's margin from collapsing through the root,
        // and relative keeps what is positioned on the root on it. The
        // root's overflow is the window's, which the margins stand in for:
        // in the copy, one window high, it would clip all below.
        style: {
          marginBlockStart: '',
          marginBlockEnd: '',
          marginInlineStart: '',
          marginInlineEnd: '',
          marginTop: `${parseFloat(root.marginTop) - window.scrollY}px`,
          marginLeft: `${parseFloat(root.marginLeft) - window.scrollX}px`,
          position: 'relative',
          display: 'flow-root',
          overflowX: 'visible',
          overflowY: 'visible',
        },
        skipFonts: true,
        cacheBust: false,
        imagePlaceholder: PLACEHOLDER_IMAGE,
        fetchRequestInit: IMAGE_REQUEST,
        filter: (node) => {
          if (node === host) return false;
          if (!placeholders && node instanceof Element) placeholders = readsBlockedImage(node, base, opaque);
          return true;
        },
      });
      // The style above reaches the copy's root only, and only where no
      // important rule of the page's comes along: the rest is set in the SVG.
      const show = overflowToShow(root, body, svg);
      const copy = await loadImage(show === null ? svg : withVisibleOverflow(svg, show));
      // Drawn as html-to-image's toCanvas draws it, on the page's background.
      const [image, context] = canvas(width * scale, height * scale);
      context.fillStyle = background;
      context.fillRect(0, 0, image.width, image.height);
      context.drawImage(copy, 0, 0, image.width, image.height);
      return { image, width, height, scale, placeholders };
    },
    draw: (capture, plan) => draw(capture.image as HTMLCanvasElement, capture.scale, plan),
    encode: (picture, quality) =>
      new Promise((resolve) => (picture as HTMLCanvasElement).toBlob(resolve, 'image/jpeg', quality)),
    show(picture) {
      const element = picture as HTMLCanvasElement;
      element.className = 'frozen';
      element.setAttribute('aria-hidden', 'true');
      return element;
    },
    objectUrl: (blob) => URL.createObjectURL(blob),
    revokeObjectUrl: (url) => URL.revokeObjectURL(url),
    async copyText(field) {
      try {
        await navigator.clipboard.writeText(field.value);
        return true;
      } catch {
        // A sandboxed page may refuse the clipboard API; the old way may work.
      }
      try {
        field.focus();
        field.select();
        return document.execCommand('copy');
      } catch {
        return false;
      }
    },
    submitForm: (form) => form.submit(),
    replaceUrl(url) {
      try {
        history.replaceState(history.state, '', url);
      } catch {
        // An opaque origin may refuse; the fragment then just stays.
      }
    },
    fetch: (url, init) => window.fetch(url, init),
    languages: () => (navigator.languages?.length ? navigator.languages : [navigator.language]),
    userAgent: () => navigator.userAgent,
    coarsePointer: () => window.matchMedia('(pointer: coarse)').matches,
  };
}
/* v8 ignore stop */
