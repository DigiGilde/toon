/**
 * How one feedback item reads on screen, in the list and in the detail sheet:
 * one place for the words, so the row and the sheet say the same thing.
 *
 * What a sender wrote (comment, quote, name, page title) comes back from these
 * as a plain string, and the templates put it in as text. None of it may reach
 * an attribute that parses markup, such as the `text` of a cell, which reads
 * `**` as bold.
 */
import type {
  FeedbackAuthor,
  FeedbackItem,
  FeedbackKind,
  FeedbackPageAddress,
  FeedbackReply,
  FeedbackSettings,
  FeedbackStatus,
  FeedbackViewport,
  Site,
  Version,
} from '@/api/types';
import { formatDate, formatTimestamp } from '@/format';
import { intlLocale, t } from '@/i18n';

/** The three views of the list: `open` is new and seen together. */
export type InboxStatus = 'open' | 'resolved' | 'all';

/** The switches of the settings that the list can send an admin to. */
export type FeedbackSwitch = 'enabled' | 'live';

/**
 * Why no page of the site carries the feedback button while feedback is on:
 * `previews` when only the previews take it and the site has none, `publish`
 * when the live site takes it too but has no version yet, and no previews
 * either. The two have different ways out.
 */
export type NoButton = 'previews' | 'publish';

/**
 * Why no page carries the button, or null when one does or feedback is off.
 * From the site's own count of previews, not from the list of them: that list
 * can fail to load, and a site with previews would then read as one without.
 */
export function noButton(
  settings: Pick<FeedbackSettings, 'enabled' | 'live'>,
  site: Pick<Site, 'previewCount' | 'hasLiveVersion'>,
): NoButton | null {
  if (!settings.enabled || site.previewCount > 0) return null;
  if (!settings.live) return 'previews';
  return site.hasLiveVersion ? null : 'publish';
}

/** The colour a status badge takes: what needs attention stands out, what is done reads as done. */
const STATUS_COLORS: Record<FeedbackStatus, 'accent' | 'neutral' | 'success'> = {
  new: 'accent',
  seen: 'neutral',
  resolved: 'success',
};

export function statusLabel(status: FeedbackStatus): string {
  return t(`feedback.status.${status}`);
}

export function statusColor(status: FeedbackStatus): 'accent' | 'neutral' | 'success' {
  return STATUS_COLORS[status];
}

export function kindLabel(kind: FeedbackKind): string {
  return t(`feedback.kind.${kind}`);
}

/** "Live" or "Preview pr-42". A preview item always carries its ref. */
export function targetLabel(item: Pick<FeedbackItem, 'target' | 'previewRef'>): string {
  return item.target === 'live'
    ? t('feedback.target.live')
    : t('feedback.target.preview', { ref: String(item.previewRef) });
}

/** The page by its title, or by its path when it has none; the root path is the home page. */
export function pageLabel(page: FeedbackPageAddress): string {
  if (page.title) return page.title;
  return page.path === '' ? t('feedback.page.root') : `/${page.path}`;
}

/** Where on the site: the page and the live site or the preview it was given on. */
export function placeText(item: Pick<FeedbackItem, 'page' | 'target' | 'previewRef'>): string {
  return t('feedback.place', { page: pageLabel(item.page), target: targetLabel(item) });
}

/**
 * Who sent it, the way a row starts. An account without a name in the login
 * goes by its address, when the reader may see that.
 */
export function authorText(author: FeedbackAuthor): string {
  if (author.kind === 'anonymous') return t('feedback.author.anonymous');
  const name = author.name ?? author.email;
  return name ? t('feedback.author.named', { name }) : t('feedback.author.accountUnnamed');
}

/** Who sent it, for the line beside "Door": the name and, for an editor, the address. */
export function authorDetail(author: FeedbackAuthor): string {
  if (author.kind === 'anonymous') return t('feedback.author.anonymous');
  if (author.name && author.email) {
    return t('feedback.meta.author.email', { name: author.name, email: author.email });
  }
  return author.name ?? author.email ?? t('feedback.author.someone');
}

/** Whether the name is one the sender typed, which nobody checked. */
export function nameUnverified(author: FeedbackAuthor): boolean {
  return author.kind === 'name';
}

/** As much of a comment as a row shows: its whitespace flattened, at most 140 characters. */
const EXCERPT_LENGTH = 140;

export function excerpt(comment: string): string {
  const flat = comment.replace(/\s+/g, ' ').trim();
  // Spread, so a character outside the basic plane is never cut in half.
  const characters = [...flat];
  if (characters.length <= EXCERPT_LENGTH) return flat;
  return `${characters.slice(0, EXCERPT_LENGTH).join('').trimEnd()}…`;
}

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** One formatter per language, as in format.ts: a list asks for one per row. */
const relativeFormats = new Map<string, Intl.RelativeTimeFormat>();

function relativeFormat(): Intl.RelativeTimeFormat {
  let format = relativeFormats.get(intlLocale.value);
  if (!format) {
    format = new Intl.RelativeTimeFormat(intlLocale.value, { numeric: 'auto' });
    relativeFormats.set(intlLocale.value, format);
  }
  return format;
}

/**
 * How long ago, while that is the quicker read: up to a week, after that the
 * date. A moment in the future (a clock that runs behind) counts as now.
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const elapsed = now - new Date(iso).getTime();
  if (elapsed < MINUTE_MS) return t('feedback.time.now');
  const format = relativeFormat();
  if (elapsed < HOUR_MS) return format.format(-Math.floor(elapsed / MINUTE_MS), 'minute');
  if (elapsed < DAY_MS) return format.format(-Math.floor(elapsed / HOUR_MS), 'hour');
  if (elapsed < 7 * DAY_MS) return format.format(-Math.floor(elapsed / DAY_MS), 'day');
  return formatDate(iso);
}

/** What follows the sender on a row: when, how many replies, and whether there is a screenshot. */
export function rowDetails(item: FeedbackItem, now: number = Date.now()): string[] {
  const parts = [relativeTime(item.createdAt, now)];
  if (item.replyCount === 1) parts.push(t('feedback.row.replies.one'));
  if (item.replyCount > 1) parts.push(t('feedback.row.replies.many', { count: item.replyCount }));
  if (item.hasScreenshot) parts.push(t('feedback.row.screenshot'));
  return parts;
}

/** The status, and who set it when, once someone did. */
export function statusDetail(item: FeedbackItem): string {
  const status = statusLabel(item.status);
  if (item.statusChangedAt === null) return status;
  const date = formatTimestamp(item.statusChangedAt);
  return item.statusChangedBy
    ? t('feedback.meta.status.changed', { status, name: item.statusChangedBy, date })
    : t('feedback.meta.status.changedUnnamed', { status, date });
}

/**
 * The version the page showed, by its date. `versions` is null when the list
 * of versions could not be read.
 */
export function versionText(versionId: string | null, versions: Version[] | null): string {
  if (versions === null) return t('feedback.meta.unknown');
  const version = versions.find((candidate) => candidate.id === versionId);
  if (!version) return t('feedback.meta.version.gone');
  const date = formatTimestamp(version.createdAt);
  return version.isLive ? t('feedback.meta.version.live', { date }) : date;
}

export function viewportText(viewport: FeedbackViewport | null): string {
  return viewport
    ? t('feedback.meta.viewport.value', { width: viewport.width, height: viewport.height })
    : t('feedback.meta.unknown');
}

/**
 * The format characters (Unicode Cf) the API refuses in a reply: soft hyphens,
 * zero-width spaces, bidi marks and the like, which text pasted from a page or
 * a document carries unseen. The zero-width joiner and non-joiner are not
 * among them: an emoji sequence or a word needs them.
 */
const REFUSED_FORMAT_CHARACTERS = /(?![\u200C\u200D])\p{Cf}/gu;

/** A reply without the format characters the API refuses, as the widget cleans what a giver types. */
export function withoutFormatCharacters(text: string): string {
  return text.replace(REFUSED_FORMAT_CHARACTERS, '');
}

/** "Siteteam, Bea Heerder", or "Siteteam" once that member is gone, or "Indiener". */
export function replyAuthor(reply: FeedbackReply): string {
  if (reply.author.kind === 'giver') return t('feedback.thread.giver');
  return reply.author.name
    ? t('feedback.thread.teamNamed', { name: reply.author.name })
    : t('feedback.thread.team');
}
