import { describe, expect, it } from 'vitest';

import { feedbackItemHref, feedbackListHref, filtersOf, queryOf } from './feedbackRoute';

describe('filtersOf', () => {
  it('reads the open items of the live site and every preview from an empty query', () => {
    expect(filtersOf({})).toEqual({ status: 'open', where: 'all' });
  });

  it('reads each status and place the list offers', () => {
    expect(filtersOf({ status: 'resolved' })).toEqual({ status: 'resolved', where: 'all' });
    expect(filtersOf({ status: 'all', target: 'live' })).toEqual({ status: 'all', where: 'live' });
    expect(filtersOf({ target: 'preview' })).toEqual({ status: 'open', where: 'preview' });
    expect(filtersOf({ target: 'preview', ref: 'pr-42' })).toEqual({ status: 'open', where: 'preview:pr-42' });
  });

  it('takes the first of a value given twice', () => {
    expect(filtersOf({ status: ['all', 'resolved'], target: ['live', 'preview'] })).toEqual({
      status: 'all',
      where: 'live',
    });
  });

  it('reads a value the list does not know as no value', () => {
    expect(filtersOf({ status: 'new', target: 'elsewhere' })).toEqual({ status: 'open', where: 'all' });
    expect(filtersOf({ status: null, target: [null] })).toEqual({ status: 'open', where: 'all' });
  });

  it('drops a ref the API would refuse, and a ref without a preview', () => {
    expect(filtersOf({ target: 'preview', ref: '../PR 42' })).toEqual({ status: 'open', where: 'preview' });
    expect(filtersOf({ target: 'preview', ref: null })).toEqual({ status: 'open', where: 'preview' });
    expect(filtersOf({ ref: 'pr-42' })).toEqual({ status: 'open', where: 'all' });
    expect(filtersOf({ target: 'live', ref: 'pr-42' })).toEqual({ status: 'open', where: 'live' });
  });
});

describe('queryOf', () => {
  it('leaves out what is the default', () => {
    expect(queryOf({ status: 'open', where: 'all' })).toEqual({});
  });

  it('says the filters the way the API takes them, and reads back the same', () => {
    for (const filters of [
      { status: 'resolved' as const, where: 'all' },
      { status: 'all' as const, where: 'live' },
      { status: 'open' as const, where: 'preview' },
      { status: 'open' as const, where: 'preview:pr-42' },
    ]) {
      expect(filtersOf(queryOf(filters))).toEqual(filters);
    }
    expect(queryOf({ status: 'all', where: 'preview:pr-42' })).toEqual({
      status: 'all',
      target: 'preview',
      ref: 'pr-42',
    });
  });
});

describe('the addresses', () => {
  it('puts the filters of the list in its query, and none when there are none', () => {
    expect(feedbackListHref('team-aurora', 'website', { status: 'open', where: 'all' })).toBe(
      '/team-aurora/website/feedback',
    );
    expect(feedbackListHref('team-aurora', 'website', { status: 'all', where: 'preview:pr-42' })).toBe(
      '/team-aurora/website/feedback?status=all&target=preview&ref=pr-42',
    );
  });

  it('gives an item a page of its own, carrying the filters for the way back', () => {
    expect(feedbackItemHref('team-aurora', 'website', 'fb-1', { status: 'open', where: 'all' })).toBe(
      '/team-aurora/website/feedback/fb-1',
    );
    expect(feedbackItemHref('team-aurora', 'website', 'a/b?c', { status: 'resolved', where: 'live' })).toBe(
      '/team-aurora/website/feedback/a%2Fb%3Fc?status=resolved&target=live',
    );
  });
});
