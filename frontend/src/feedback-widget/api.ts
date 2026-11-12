/**
 * The widget API on the content host (/-/feedback/v1). Every call carries the
 * page token in `Authorization: Toon-Feedback <token>` and never a cookie
 * (`credentials: 'omit'`); the answers are read defensively, and anything the
 * widget cannot read counts as "try again later".
 */
import { isLocalPath } from './config';
import { parseHighlight } from './fragment';
import { formatWait, type Params, type Translate, type WidgetKey } from './i18n';
import { collapse, cut } from './text';
import type { Anchor, Highlight, Kind, Lang, Rect, Status } from './types';

export type Author = { kind: 'account' } | { kind: 'name'; name: string } | { kind: 'anonymous' };

export interface NewItem {
  kind: Kind;
  comment: string;
  page: { path: string; title: string };
  anchor: Anchor | null;
  viewport: { width: number; height: number; dpr: number };
  author: Author;
  screenshot: { region: Rect | null };
}

export interface MineReply {
  id: string;
  author: 'team' | 'you';
  name: string | null;
  body: string;
  createdAt: string;
}

export interface MineItem {
  id: string;
  kind: Kind;
  status: Status;
  comment: string;
  quote: string | null;
  anchor: Highlight | null;
  page: { path: string; title: string | null; url: string | null };
  createdAt: string;
  /** The latest replies, oldest first: at most ten come along. */
  replies: MineReply[];
  /** How many replies the item has in all. */
  replyCount: number;
}

/** The giver's items, newest first, and whether older ones were left out. */
export interface MineList {
  items: MineItem[];
  more: boolean;
}

export type Failure =
  | { type: 'problem'; status: number; code: string; detail: string | null; retryAfter: number | null }
  | { type: 'unreadable'; status: number }
  | { type: 'network' };

export class ApiFailure extends Error {
  constructor(readonly failure: Failure) {
    super(failure.type);
  }
}

export interface ApiOptions {
  api: string;
  token: string;
  lang: Lang;
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  now: () => number;
}

export interface Api {
  create(item: NewItem, screenshot: Blob | null): Promise<string>;
  mine(): Promise<MineList>;
  reply(id: string, body: string): Promise<MineReply>;
  remove(id: string): Promise<void>;
}

const KINDS: readonly Kind[] = ['page', 'point', 'text'];
const STATUSES: readonly Status[] = ['new', 'seen', 'resolved'];
const MAX_DETAIL = 300;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return null;
  return typeof value === 'string' ? value : undefined;
}

/** Seconds until a retry, from delta-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null, now: number): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(0, (date - now) / 1000);
}

async function failureOf(response: Response, now: number): Promise<Failure> {
  const unreadable: Failure = { type: 'unreadable', status: response.status };
  const type = response.headers.get('Content-Type') ?? '';
  if (!/^application\/(problem\+)?json\b/i.test(type)) return unreadable;
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    return unreadable;
  }
  if (!isObject(data) || typeof data.code !== 'string') return unreadable;
  return {
    type: 'problem',
    status: response.status,
    code: data.code,
    detail: typeof data.detail === 'string' ? cut(collapse(data.detail).trim(), MAX_DETAIL) || null : null,
    retryAfter: parseRetryAfter(response.headers.get('Retry-After'), now),
  };
}

export function parseReply(value: unknown): MineReply | null {
  if (!isObject(value)) return null;
  const { id, author, body, createdAt } = value;
  const name = stringOrNull(value.name);
  if (typeof id !== 'string' || id.length === 0) return null;
  if (author !== 'team' && author !== 'you') return null;
  if (typeof body !== 'string' || typeof createdAt !== 'string' || name === undefined) return null;
  return { id, author, name: author === 'team' ? name : null, body, createdAt };
}

export function parseItem(value: unknown): MineItem | null {
  if (!isObject(value) || !isObject(value.page)) return null;
  const { id, kind, status, comment, createdAt, replies } = value;
  const quote = stringOrNull(value.quote);
  const title = stringOrNull(value.page.title);
  const url = value.page.url;
  const path = value.page.path;
  if (typeof id !== 'string' || id.length === 0) return null;
  if (!KINDS.includes(kind as Kind) || !STATUSES.includes(status as Status)) return null;
  if (typeof comment !== 'string' || typeof createdAt !== 'string' || typeof path !== 'string') return null;
  if (quote === undefined || title === undefined || !Array.isArray(replies)) return null;
  const shown = replies.map(parseReply).filter((reply): reply is MineReply => reply !== null);
  // A count it cannot read is never fewer than the replies that came along.
  const count = Number.isInteger(value.replyCount) ? (value.replyCount as number) : 0;
  return {
    id,
    kind: kind as Kind,
    status: status as Status,
    comment,
    quote,
    anchor: parseHighlight(value.anchor),
    page: { path, title, url: typeof url === 'string' && isLocalPath(url) ? url : null },
    createdAt,
    replies: shown,
    replyCount: Math.max(count, shown.length),
  };
}

export function createApi(options: ApiOptions): Api {
  async function call(path: string, init: RequestInit): Promise<Response> {
    let response: Response;
    try {
      response = await options.fetch(`${options.api}${path}`, {
        ...init,
        credentials: 'omit',
        cache: 'no-store',
        headers: {
          ...(init.headers as Record<string, string> | undefined),
          Authorization: `Toon-Feedback ${options.token}`,
          'Accept-Language': options.lang,
        },
      });
    } catch {
      throw new ApiFailure({ type: 'network' });
    }
    if (!response.ok) throw new ApiFailure(await failureOf(response, options.now()));
    return response;
  }

  async function json(response: Response): Promise<Record<string, unknown>> {
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!isObject(data)) throw new ApiFailure({ type: 'unreadable', status: response.status });
    return data;
  }

  const itemPath = (id: string) => `/items/${encodeURIComponent(id)}`;

  return {
    async create(item, screenshot) {
      const body = new FormData();
      body.append('item', JSON.stringify(item));
      if (screenshot !== null) body.append('screenshot', screenshot, 'screenshot.jpg');
      const response = await call('/items', { method: 'POST', body });
      const data = await json(response);
      if (typeof data.id !== 'string') throw new ApiFailure({ type: 'unreadable', status: response.status });
      return data.id;
    },
    async mine() {
      const response = await call('/items', { method: 'GET' });
      const data = await json(response);
      if (!Array.isArray(data.items)) throw new ApiFailure({ type: 'unreadable', status: response.status });
      return {
        items: data.items.map(parseItem).filter((item): item is MineItem => item !== null),
        more: data.more === true,
      };
    },
    async reply(id, text) {
      const response = await call(`${itemPath(id)}/replies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: text }),
      });
      const reply = parseReply(await json(response));
      if (reply === null) throw new ApiFailure({ type: 'unreadable', status: response.status });
      return reply;
    },
    async remove(id) {
      await call(itemPath(id), { method: 'DELETE' });
    },
  };
}

export type Action = 'send' | 'reply' | 'list' | 'delete';

export interface ErrorView {
  message: string;
  /** The remedy is a reload: offer to copy what was typed. */
  copy: boolean;
  /** The screenshot was the problem: offer to send without it. */
  withoutScreenshot: boolean;
}

/** What the giver reads when a call fails, per problem code and action. */
export function describeFailure(error: unknown, action: Action, t: Translate): ErrorView {
  const writes = action === 'send' || action === 'reply';
  const view = (key: WidgetKey, extra: Partial<ErrorView> = {}, params?: Params): ErrorView => ({
    message: t(key, params),
    copy: false,
    withoutScreenshot: false,
    ...extra,
  });
  if (!(error instanceof ApiFailure) || error.failure.type !== 'problem') return view('error.later');
  const { code, detail, retryAfter } = error.failure;
  switch (code) {
    case 'FEEDBACK_UNAVAILABLE':
      return writes ? view('error.unavailable', { copy: true }) : view('error.unavailable.other');
    case 'FEEDBACK_ACCOUNT_REQUIRED':
      return writes ? view('error.account', { copy: true }) : view('error.account.other');
    case 'FEEDBACK_PAGE_MOVED':
      // The page was served before its site or group got a new address.
      return writes ? view('error.moved', { copy: true }) : view('error.moved.other');
    case 'FEEDBACK_INVALID':
      return detail === null ? view('error.invalid') : { ...view('error.invalid'), message: detail };
    case 'FEEDBACK_AUTHOR_NOT_ALLOWED':
      return view('error.author', { copy: true });
    case 'FEEDBACK_SCREENSHOT_TOO_LARGE':
      return view('error.shot.large', { withoutScreenshot: true });
    case 'FEEDBACK_SCREENSHOT_INVALID':
      return view('error.shot.invalid', { withoutScreenshot: true });
    case 'FEEDBACK_SCREENSHOT_QUOTA':
      return view('error.shot.quota', { withoutScreenshot: true });
    case 'FEEDBACK_SITE_FULL':
      return view('error.full');
    case 'FEEDBACK_RATE_LIMITED':
    case 'TOO_MANY_REQUESTS':
      return retryAfter === null
        ? view('error.rate.soon')
        : view('error.rate', {}, { time: formatWait(retryAfter, t) });
    case 'FEEDBACK_BUSY':
      return retryAfter === null
        ? view('error.busy.soon')
        : view('error.busy', {}, { time: formatWait(retryAfter, t) });
    case 'FEEDBACK_TIMEOUT':
      // A screenshot is what makes a request slow: offer to send without it.
      return view('error.timeout', { withoutScreenshot: true });
    case 'FEEDBACK_THREAD_FULL':
      return view('error.threadFull');
    default:
      return view('error.later');
  }
}
