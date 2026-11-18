/**
 * The addresses of the feedback list and of one item. The list's filters live
 * in its query, the way the API takes them (`status`, `target`, `ref`), so a
 * link from elsewhere lands on a filtered list and the way back to it keeps
 * them. What the query does not say is the default: the open items of the live
 * site and every preview.
 */
import type { LocationQuery, Router } from 'vue-router';

import type { InboxStatus } from '@/components/site/feedbackText';
import { SLUG_RE } from '@/composables/slug';

/** The value of the place filter that narrows the list to one preview, before its ref. */
export const ONE_PREVIEW = 'preview:';

/** The status and the place the list is narrowed to, as the inbox holds them. */
export interface FeedbackFilters {
  status: InboxStatus;
  /** `all`, `live`, `preview`, or ONE_PREVIEW followed by the ref of one preview. */
  where: string;
}

function single(value: LocationQuery[string]): string | null {
  const first = Array.isArray(value) ? value[0] : value;
  return typeof first === 'string' ? first : null;
}

/**
 * The filters a query asks for. A value the list does not know counts as not
 * there, and so does a ref that is no slug, which the API would refuse.
 */
export function filtersOf(query: LocationQuery): FeedbackFilters {
  const status = single(query.status);
  const target = single(query.target);
  const ref = single(query.ref);
  let where = 'all';
  if (target === 'live') where = 'live';
  else if (target === 'preview') where = ref !== null && SLUG_RE.test(ref) ? `${ONE_PREVIEW}${ref}` : 'preview';
  return { status: status === 'resolved' || status === 'all' ? status : 'open', where };
}

/** The query of the filters, without what is the default anyway. */
export function queryOf(filters: FeedbackFilters): Record<string, string> {
  const query: Record<string, string> = {};
  if (filters.status !== 'open') query.status = filters.status;
  if (filters.where.startsWith(ONE_PREVIEW)) {
    query.target = 'preview';
    query.ref = filters.where.slice(ONE_PREVIEW.length);
  } else if (filters.where !== 'all') {
    query.target = filters.where;
  }
  return query;
}

function withQuery(path: string, query: Record<string, string>): string {
  const search = new URLSearchParams(query).toString();
  return search ? `${path}?${search}` : path;
}

/** The feedback list of a site, narrowed as `filters` say. */
export function feedbackListHref(group: string, site: string, filters: FeedbackFilters): string {
  return withQuery(`/${group}/${site}/feedback`, queryOf(filters));
}

/**
 * The mark the page of an item leaves on its way back to the list after
 * deleting it, so the list says so. It lives in the history entry's state, as
 * publishedMark does, which only this origin can write.
 */
const DELETED_MARK = 'toonFeedbackDeleted';

/** Back to the list at `href` in place of the page of an item that is gone now, as the page that deleted it. */
export function markDeleted(router: Router, href: string): Promise<unknown> {
  const { path, query } = router.resolve(href);
  return router.replace({ path, query, state: { [DELETED_MARK]: true } });
}

/**
 * Whether the current visit carries the mark. Taking it clears it, so a reload
 * or a return through the back button says nothing again.
 */
export function takeDeletedMark(router: Router): boolean {
  const history = router.options.history;
  if (history.state[DELETED_MARK] !== true) return false;
  history.replace(history.location, { ...history.state, [DELETED_MARK]: false });
  return true;
}

/** The page of one item, carrying the filters of the list it was opened from for the way back. */
export function feedbackItemHref(group: string, site: string, id: string, filters: FeedbackFilters): string {
  return withQuery(`/${group}/${site}/feedback/${encodeURIComponent(id)}`, queryOf(filters));
}
