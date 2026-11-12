import { afterEach, describe, expect, it } from 'vitest';

import type { FeedbackAuthor, FeedbackItem, Version } from '@/api/types';
import { formatDate, formatTimestamp } from '@/format';
import { _setLocaleForTest } from '@/i18n';

import {
  authorDetail,
  authorText,
  excerpt,
  kindLabel,
  nameUnverified,
  noButton,
  pageLabel,
  placeText,
  relativeTime,
  replyAuthor,
  rowDetails,
  statusColor,
  statusDetail,
  statusLabel,
  targetLabel,
  versionText,
  viewportText,
  withoutFormatCharacters,
} from './feedbackText';

afterEach(() => {
  _setLocaleForTest('nl');
});

function item(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    id: 'fb-9',
    kind: 'point',
    status: 'new',
    comment: 'Klopt dit?',
    quote: null,
    page: { path: 'docs/', title: 'Documentatie', url: 'https://sites.toon.test/g/s/docs/' },
    target: 'live',
    previewRef: null,
    versionId: 'versie-1',
    author: { kind: 'anonymous', name: null, email: null },
    hasScreenshot: false,
    replyCount: 0,
    createdAt: '2026-07-18T09:15:00.000Z',
    statusChangedAt: null,
    statusChangedBy: null,
    ...overrides,
  };
}

function version(id: string, isLive: boolean): Version {
  return {
    id,
    siteSlug: 's',
    groupSlug: 'g',
    target: 'live',
    storageRef: `g/s/${id}`,
    origin: 'upload',
    createdByMember: null,
    createdByName: null,
    createdByRepository: null,
    createdAt: '2026-07-17T14:32:00.000Z',
    isLive,
  };
}

const ACCOUNT: FeedbackAuthor = { kind: 'account', name: 'Ada Vermeer', email: 'ada@voorbeeld.nl' };

describe('the words for a status, a kind and a target', () => {
  it('names each status, and colours what needs attention apart from what is done', () => {
    expect([statusLabel('new'), statusLabel('seen'), statusLabel('resolved')]).toEqual([
      'Nieuw',
      'Gezien',
      'Afgehandeld',
    ]);
    expect([statusColor('new'), statusColor('seen'), statusColor('resolved')]).toEqual([
      'accent',
      'neutral',
      'success',
    ]);
  });

  it('names each kind', () => {
    expect([kindLabel('page'), kindLabel('point'), kindLabel('text')]).toEqual(['Hele pagina', 'Punt', 'Tekst']);
  });

  it('says live, or which preview', () => {
    expect(targetLabel({ target: 'live', previewRef: null })).toBe('Live');
    expect(targetLabel({ target: 'preview', previewRef: 'pr-42' })).toBe('Preview pr-42');
  });

  it('speaks English when the interface does', () => {
    _setLocaleForTest('en');

    expect(statusLabel('resolved')).toBe('Resolved');
    expect(kindLabel('page')).toBe('Whole page');
  });
});

describe('the page', () => {
  it('goes by its title', () => {
    expect(pageLabel({ path: 'docs/', title: 'Documentatie', url: '' })).toBe('Documentatie');
  });

  it('goes by its path without a title, and an empty title is none', () => {
    expect(pageLabel({ path: 'docs/', title: null, url: '' })).toBe('/docs/');
    expect(pageLabel({ path: 'docs/', title: '', url: '' })).toBe('/docs/');
  });

  it('calls the root of the site the home page', () => {
    expect(pageLabel({ path: '', title: null, url: '' })).toBe('Startpagina');
  });

  it('puts the page and the place together', () => {
    expect(placeText(item({ target: 'preview', previewRef: 'pr-42' }))).toBe('Documentatie (Preview pr-42)');
  });
});

describe('the sender', () => {
  it('says nothing about someone who sent it without a name', () => {
    const anonymous: FeedbackAuthor = { kind: 'anonymous', name: null, email: null };

    expect(authorText(anonymous)).toBe('Zonder naam');
    expect(authorDetail(anonymous)).toBe('Zonder naam');
    expect(nameUnverified(anonymous)).toBe(false);
  });

  it('marks a name the sender typed as unchecked', () => {
    const typed: FeedbackAuthor = { kind: 'name', name: 'Jan', email: null };

    expect(authorText(typed)).toBe('Door Jan');
    expect(authorDetail(typed)).toBe('Jan');
    expect(nameUnverified(typed)).toBe(true);
    expect(nameUnverified(ACCOUNT)).toBe(false);
  });

  it('gives the name of an account, with the address beside it for an editor', () => {
    expect(authorText(ACCOUNT)).toBe('Door Ada Vermeer');
    expect(authorDetail(ACCOUNT)).toBe('Ada Vermeer, ada@voorbeeld.nl');
    expect(authorDetail({ ...ACCOUNT, email: null })).toBe('Ada Vermeer');
  });

  it('goes by the address for an account without a name in the login', () => {
    const unnamed: FeedbackAuthor = { kind: 'account', name: null, email: 'ada@voorbeeld.nl' };

    expect(authorText(unnamed)).toBe('Door ada@voorbeeld.nl');
    expect(authorDetail(unnamed)).toBe('ada@voorbeeld.nl');
  });

  it('still says it was an account when a reader sees neither name nor address', () => {
    const hidden: FeedbackAuthor = { kind: 'account', name: null, email: null };

    expect(authorText(hidden)).toBe('Door iemand met een account');
    expect(authorDetail(hidden)).toBe('Iemand met een account');
  });
});

describe('excerpt', () => {
  it('leaves a short comment as it is, with its whitespace flattened', () => {
    expect(excerpt('Klopt dit bedrag?')).toBe('Klopt dit bedrag?');
    expect(excerpt('  Klopt\n\ndit\tbedrag?  ')).toBe('Klopt dit bedrag?');
  });

  it('cuts a long comment at 140 characters, without a space before the ellipsis', () => {
    const long = `${'a'.repeat(139)} ${'b'.repeat(50)}`;

    expect(excerpt('a'.repeat(140))).toBe('a'.repeat(140));
    expect(excerpt(long)).toBe(`${'a'.repeat(139)}…`);
    expect(excerpt('c'.repeat(141))).toBe(`${'c'.repeat(140)}…`);
  });

  it('never cuts a character outside the basic plane in half', () => {
    const emoji = '😀'.repeat(141);

    expect(excerpt(emoji)).toBe(`${'😀'.repeat(140)}…`);
  });
});

describe('relativeTime', () => {
  const at = Date.parse('2026-07-18T12:00:00.000Z');

  it('says just now within a minute, and for a moment in the future', () => {
    expect(relativeTime('2026-07-18T11:59:30.000Z', at)).toBe('zojuist');
    expect(relativeTime('2026-07-18T12:05:00.000Z', at)).toBe('zojuist');
  });

  it('counts minutes, hours and days', () => {
    expect(relativeTime('2026-07-18T11:55:00.000Z', at)).toBe('5 minuten geleden');
    expect(relativeTime('2026-07-18T09:00:00.000Z', at)).toBe('3 uur geleden');
    expect(relativeTime('2026-07-17T11:00:00.000Z', at)).toBe('gisteren');
    expect(relativeTime('2026-07-14T12:00:00.000Z', at)).toBe('4 dagen geleden');
  });

  it('gives the date from a week on', () => {
    expect(relativeTime('2026-07-11T12:00:00.000Z', at)).toBe(formatDate('2026-07-11T12:00:00.000Z'));
  });

  it('speaks English when the interface does', () => {
    _setLocaleForTest('en');

    expect(relativeTime('2026-07-18T11:59:30.000Z', at)).toBe('just now');
    expect(relativeTime('2026-07-18T09:00:00.000Z', at)).toBe('3 hours ago');
  });
});

describe('rowDetails', () => {
  const at = Date.parse('2026-07-18T12:00:00.000Z');

  it('says only when, without replies or a screenshot', () => {
    expect(rowDetails(item({ createdAt: '2026-07-18T09:00:00.000Z' }), at)).toEqual(['3 uur geleden']);
  });

  it('counts one reply, several replies, and says there is a screenshot', () => {
    expect(rowDetails(item({ createdAt: '2026-07-18T09:00:00.000Z', replyCount: 1 }), at)).toEqual([
      '3 uur geleden',
      '1 reactie',
    ]);
    expect(
      rowDetails(item({ createdAt: '2026-07-18T09:00:00.000Z', replyCount: 3, hasScreenshot: true }), at),
    ).toEqual(['3 uur geleden', '3 reacties', 'met schermafbeelding']);
  });

  it('reads the clock itself when not given one', () => {
    expect(rowDetails(item({ createdAt: new Date().toISOString() }))).toEqual(['zojuist']);
  });
});

describe('statusDetail', () => {
  it('is the status alone while nobody changed it', () => {
    expect(statusDetail(item())).toBe('Nieuw');
  });

  it('says who changed it when', () => {
    const changed = item({
      status: 'resolved',
      statusChangedAt: '2026-07-18T10:00:00.000Z',
      statusChangedBy: 'Bea Heerder',
    });

    expect(statusDetail(changed)).toBe(
      `Afgehandeld, door Bea Heerder op ${formatTimestamp('2026-07-18T10:00:00.000Z')}`,
    );
  });

  it('says when without a name once the member who changed it is gone', () => {
    const changed = item({ status: 'seen', statusChangedAt: '2026-07-18T10:00:00.000Z' });

    expect(statusDetail(changed)).toBe(`Gezien op ${formatTimestamp('2026-07-18T10:00:00.000Z')}`);
  });
});

describe('versionText', () => {
  it('says unknown when the versions could not be read', () => {
    expect(versionText('versie-1', null)).toBe('Onbekend');
  });

  it('says the version is gone when it is no longer among them, or was never known', () => {
    expect(versionText('versie-weg', [version('versie-1', true)])).toBe('Niet meer beschikbaar');
    expect(versionText(null, [version('versie-1', true)])).toBe('Niet meer beschikbaar');
  });

  it('gives the date, and says so when that version is live now', () => {
    const date = formatTimestamp('2026-07-17T14:32:00.000Z');

    expect(versionText('versie-1', [version('versie-1', true)])).toBe(`${date}, nu live`);
    expect(versionText('versie-0', [version('versie-0', false)])).toBe(date);
  });
});

describe('viewportText', () => {
  it('gives the window size in pixels, or says it is unknown', () => {
    expect(viewportText({ width: 1280, height: 800, dpr: 2 })).toBe('1280 bij 800 pixels');
    expect(viewportText(null)).toBe('Onbekend');
  });
});

describe('replyAuthor', () => {
  it('tells the sender from the site team, with the name of the member while there is one', () => {
    const at = '2026-07-18T10:00:00.000Z';

    expect(replyAuthor({ id: 'r', author: { kind: 'giver', name: null }, body: 'x', createdAt: at })).toBe(
      'Indiener',
    );
    expect(replyAuthor({ id: 'r', author: { kind: 'member', name: 'Bea' }, body: 'x', createdAt: at })).toBe(
      'Siteteam, Bea',
    );
    expect(replyAuthor({ id: 'r', author: { kind: 'member', name: null }, body: 'x', createdAt: at })).toBe(
      'Siteteam',
    );
  });
});

describe('withoutFormatCharacters', () => {
  it('drops the format characters the API refuses, wherever they stand', () => {
    expect(withoutFormatCharacters('\u00ADzorg\u00ADwet\u200B \u2060a\uFEFFb\u200E\u200F\u061C\u202Ac\u202E\u2066d\u2069\u{E0001}\u{E0041}\u{E007F}')).toBe(
      'zorgwet abcd',
    );
  });

  it('keeps the zero-width joiner and non-joiner, which an emoji sequence or a word needs', () => {
    const technologist = '\u{1F9D1}\u200D\u{1F4BB}';
    const persian = '\u0645\u06CC\u200C\u062E\u0648\u0627\u0647\u0645';
    expect(withoutFormatCharacters(`${technologist} ${persian}`)).toBe(`${technologist} ${persian}`);
    expect(withoutFormatCharacters(`\u200D\u00AD\u200C`)).toBe('\u200D\u200C');
  });

  it('leaves everything else as it is, whitespace and other invisible characters included', () => {
    const text = '  Dank je,\n\twe passen het aan.\u00A0\uFE0F\u{1F44D}\u{1F3FD}\u{E0100}  ';
    expect(withoutFormatCharacters(text)).toBe(text);
  });
});

describe('noButton', () => {
  const WITH_PREVIEWS = { previewCount: 2, hasLiveVersion: true };
  const BARE = { previewCount: 0, hasLiveVersion: true };
  const NEVER_PUBLISHED = { previewCount: 0, hasLiveVersion: false };

  it('finds a page with the button wherever there is a preview, or a live version that takes feedback', () => {
    expect(noButton({ enabled: true, live: false }, WITH_PREVIEWS)).toBeNull();
    expect(noButton({ enabled: true, live: true }, { previewCount: 1, hasLiveVersion: false })).toBeNull();
    expect(noButton({ enabled: true, live: true }, BARE)).toBeNull();
  });

  it('names no reason while feedback is off', () => {
    expect(noButton({ enabled: false, live: false }, BARE)).toBeNull();
    expect(noButton({ enabled: false, live: true }, NEVER_PUBLISHED)).toBeNull();
  });

  it('tells a site without previews whose live site takes no feedback from one that has nothing live yet', () => {
    expect(noButton({ enabled: true, live: false }, BARE)).toBe('previews');
    expect(noButton({ enabled: true, live: false }, NEVER_PUBLISHED)).toBe('previews');
    expect(noButton({ enabled: true, live: true }, NEVER_PUBLISHED)).toBe('publish');
  });
});
