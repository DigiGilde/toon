import { mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { FeedbackCounts, FeedbackItem } from '@/api/types';
import { _setLocaleForTest } from '@/i18n';

import FeedbackInbox from './FeedbackInbox.vue';
import { fireDetailEvent } from './testHelpers';

afterEach(() => {
  _setLocaleForTest('nl');
  vi.restoreAllMocks();
});

function item(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    id: 'fb-1',
    kind: 'text',
    status: 'new',
    comment: 'Klopt dit bedrag?',
    quote: '2,4 miljoen',
    page: { path: 'docs/', title: 'Documentatie', url: 'https://sites.toon.test/g/s/_preview/pr-42/docs/' },
    target: 'preview',
    previewRef: 'pr-42',
    versionId: null,
    author: { kind: 'name', name: 'Jan', email: null },
    hasScreenshot: true,
    replyCount: 2,
    createdAt: new Date().toISOString(),
    statusChangedAt: null,
    statusChangedBy: null,
    ...overrides,
  };
}

const COUNTS: FeedbackCounts = { new: 1, seen: 1, resolved: 1 };

type Props = InstanceType<typeof FeedbackInbox>['$props'];

function makeWrapper(props: Partial<Props> = {}) {
  return mount(FeedbackInbox, {
    props: {
      items: [item(), item({ id: 'fb-2', status: 'seen', author: { kind: 'anonymous', name: null, email: null } })],
      counts: COUNTS,
      status: 'open',
      where: 'all',
      previewRefs: null,
      loaded: true,
      loading: false,
      failed: null,
      nextCursor: null,
      moreBusy: false,
      enabled: true,
      noButton: null,
      canAdmin: true,
      ...props,
    },
  });
}

/** The text on each status button, in order. */
function statusLabels(wrapper: ReturnType<typeof makeWrapper>): (string | undefined)[] {
  return wrapper.findAll('[data-testid="feedback-status"] nldd-toggle-button').map((choice) => choice.attributes('text'));
}

describe('FeedbackInbox: before the first page', () => {
  it('holds the place with a loading message, and offers no filters yet', () => {
    const wrapper = makeWrapper({ loaded: false, items: [], counts: { new: 0, seen: 0, resolved: 0 } });

    const holder = wrapper.find('[data-testid="feedback-first-load"]');
    expect(holder.attributes('variant')).toBe('loading');
    expect(holder.attributes('text')).toBe('Feedback laden');
    expect(wrapper.find('[data-testid="feedback-status"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-list"]').exists()).toBe(false);
    // Not "nothing yet" while nothing is known.
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(false);
  });
});

describe('FeedbackInbox: the rows', () => {
  it('shows one row per item that opens it, with the place, the comment and the status', async () => {
    const wrapper = makeWrapper();

    const rows = wrapper.findAll('[data-testid="feedback-list"] nldd-list-item');
    expect(rows).toHaveLength(2);
    expect(rows[0]!.attributes('button')).toBeDefined();
    expect(rows[0]!.find('[slot="overline"]').text()).toBe('Documentatie (Preview pr-42)');
    expect(rows[0]!.find('nldd-text-cell').text()).toContain('Klopt dit bedrag?');
    const badge = wrapper.find('[data-testid="feedback-badge-fb-1"]');
    expect(badge.attributes('text')).toBe('Nieuw');
    expect(badge.attributes('color')).toBe('accent');
    expect(wrapper.find('[data-testid="feedback-badge-fb-2"]').attributes('text')).toBe('Gezien');
    expect(rows[0]!.find('nldd-icon-cell').attributes('icon')).toBe('chevron-right');

    await rows[1]!.trigger('click');
    expect(wrapper.emitted('open')).toEqual([[expect.objectContaining({ id: 'fb-2' })]]);
  });

  it('says who sent it, when, how many replies there are and whether there is a screenshot', () => {
    const wrapper = makeWrapper();

    const line = wrapper.find('[data-testid="feedback-fb-1"] [slot="supporting-text"]');
    expect(line.text().replace(/\s+/g, ' ')).toContain('Door Jan');
    expect(line.text()).toContain(', zojuist, 2 reacties, met schermafbeelding');
    // A typed name is marked as one nobody checked.
    expect(line.find('nldd-tag').attributes('text')).toBe('Naam niet gecontroleerd');
    const anonymous = wrapper.find('[data-testid="feedback-fb-2"] [slot="supporting-text"]');
    expect(anonymous.text()).toMatch(/^Zonder naam, /);
    expect(anonymous.find('nldd-tag').exists()).toBe(false);
  });

  it('puts what the sender wrote in as text, never in an attribute that formats it', () => {
    const wrapper = makeWrapper({
      items: [item({ comment: '**vet** <b>niet</b>', page: { path: 'x/', title: '**titel**', url: '' } })],
    });

    const cell = wrapper.find('[data-testid="feedback-fb-1"] nldd-text-cell');
    expect(cell.attributes('text')).toBeUndefined();
    expect(cell.attributes('overline')).toBeUndefined();
    expect(cell.attributes('supporting-text')).toBeUndefined();
    expect(cell.text()).toContain('**vet** <b>niet</b>');
    expect(cell.find('b').exists()).toBe(false);
    expect(cell.find('[slot="overline"]').text()).toBe('**titel** (Preview pr-42)');
  });

  it('shows only the first 140 characters of a long comment', () => {
    const wrapper = makeWrapper({ items: [item({ comment: 'a'.repeat(200) })] });

    expect(wrapper.find('[data-testid="feedback-fb-1"] nldd-text-cell').text()).toContain(`${'a'.repeat(140)}…`);
  });
});

describe('FeedbackInbox: the filters', () => {
  it('offers open, resolved and all as one choice, each with its count and the current one selected', () => {
    const wrapper = makeWrapper();

    const control = wrapper.find('[data-testid="feedback-status"]');
    expect(control.element.tagName).toBe('NLDD-TOGGLE-BUTTON-GROUP');
    expect(control.attributes('type')).toBe('radio');
    expect(control.attributes('accessible-label')).toBe('Status');
    const choices = control
      .findAll('nldd-toggle-button')
      .map((choice) => [choice.attributes('value'), choice.attributes('selected') !== undefined]);
    expect(choices).toEqual([
      ['open', true],
      ['resolved', false],
      ['all', false],
    ]);
    expect(statusLabels(wrapper)).toEqual(['Open (2)', 'Afgehandeld (1)', 'Alles (3)']);
  });

  it('moves the selection along with the status it is given', async () => {
    const wrapper = makeWrapper();

    await wrapper.setProps({ status: 'all' });

    expect(wrapper.find('[data-testid="feedback-status-all"]').attributes('selected')).toBeDefined();
    expect(wrapper.find('[data-testid="feedback-status-open"]').attributes('selected')).toBeUndefined();
  });

  it('passes on another status, and nothing for the one already chosen, an unknown one or a button let go', () => {
    const wrapper = makeWrapper();
    const control = wrapper.find('[data-testid="feedback-status"]').element;

    fireDetailEvent(control, 'change', { selected: false, value: 'all' });
    fireDetailEvent(control, 'change', { selected: true, value: 'resolved' });
    fireDetailEvent(control, 'change', { selected: true, value: 'open' });
    fireDetailEvent(control, 'change', { selected: true, value: 'niets' });
    control.dispatchEvent(new CustomEvent('change'));

    expect(wrapper.emitted('update:status')).toEqual([['resolved']]);
  });

  it.each([
    ['the live site', 'live', [item({ id: 'fb-live', target: 'live', previewRef: null })], 'feedback-list'],
    ['all previews', 'preview', [item()], 'feedback-list'],
    ['one preview without feedback', 'preview:pr-43', [], 'feedback-empty-filter'],
  ])('drops the site-wide counts from the statuses while the list shows %s', (_name, where, items, shown) => {
    const wrapper = makeWrapper({ where, items, previewRefs: ['pr-42', 'pr-43'] });

    expect(statusLabels(wrapper)).toEqual(['Open', 'Afgehandeld', 'Alles']);
    // The counts still decide what the list says, so a place without feedback
    // is no site without feedback, and the filters stay to choose another.
    expect(wrapper.find(`[data-testid="${shown}"]`).exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-where"]').exists()).toBe(true);
  });

  it.each([
    ['no preview left', []],
    ['previews that could not be read', null],
  ])('offers the live site and all previews on a site with feedback and %s', async (_name, previewRefs) => {
    // Items outlive the preview they were given on, so the list can still mix both.
    const wrapper = makeWrapper({ previewRefs });

    const select = wrapper.find<HTMLSelectElement>('[data-testid="feedback-where"]');
    expect(select.findAll('option').map((option) => [option.attributes('value'), option.text()])).toEqual([
      ['all', 'Live en previews'],
      ['live', 'De live site'],
      ['preview', 'Alle previews'],
    ]);
    expect(select.element.value).toBe('all');
    await select.setValue('live');
    expect(wrapper.emitted('update:where')).toEqual([['live']]);
  });

  it.each(['live', 'preview'])('keeps the place filter while the list is narrowed to %s, also once no preview is left', async (where) => {
    const wrapper = makeWrapper({ previewRefs: [], where });

    const select = wrapper.find<HTMLSelectElement>('[data-testid="feedback-where"]');
    expect(select.findAll('option').map((option) => option.attributes('value'))).toEqual(['all', 'live', 'preview']);
    expect(select.element.value).toBe(where);
    // The way back to the whole list.
    await select.setValue('all');
    expect(wrapper.emitted('update:where')).toEqual([['all']]);
  });

  it('offers the live site, all previews and each preview, and passes the choice on', async () => {
    const wrapper = makeWrapper({ previewRefs: ['pr-42', 'pr-43'], where: 'live' });

    const select = wrapper.find<HTMLSelectElement>('[data-testid="feedback-where"]');
    expect(select.attributes('aria-label')).toBe('Gegeven op');
    expect(select.findAll('option').map((option) => [option.attributes('value'), option.text()])).toEqual([
      ['all', 'Live en previews'],
      ['live', 'De live site'],
      ['preview', 'Alle previews'],
      ['preview:pr-42', 'Preview pr-42'],
      ['preview:pr-43', 'Preview pr-43'],
    ]);
    expect(select.element.value).toBe('live');

    await select.setValue('preview:pr-43');
    expect(wrapper.emitted('update:where')).toEqual([['preview:pr-43']]);
  });

  it('offers no filters on a site that has no feedback at all', () => {
    const wrapper = makeWrapper({ items: [], counts: { new: 0, seen: 0, resolved: 0 }, previewRefs: ['pr-42'] });

    expect(wrapper.find('[data-testid="feedback-status"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-where"]').exists()).toBe(false);
  });

  it('covers the list while a new filter loads, and keeps the filters out of that', () => {
    const wrapper = makeWrapper({ loading: true });

    const indicator = wrapper.find('nldd-activity-indicator');
    expect(indicator.attributes('complete')).toBeUndefined();
    expect(indicator.find('[data-testid="feedback-list"]').exists()).toBe(true);
    expect(indicator.find('[data-testid="feedback-status"]').exists()).toBe(false);

    const done = makeWrapper({ loading: false });
    expect(done.find('nldd-activity-indicator').attributes('complete')).toBeDefined();
  });
});

describe('FeedbackInbox: nothing to show', () => {
  const NONE: FeedbackCounts = { new: 0, seen: 0, resolved: 0 };

  it('tells an admin that feedback is off and takes them to the switch', async () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, enabled: false, canAdmin: true });

    const empty = wrapper.find('[data-testid="feedback-empty-off"]');
    expect(empty.attributes('text')).toBe('Feedback staat uit');
    expect(empty.attributes('supporting-text')).toContain('Zet feedback aan onder Feedback verzamelen.');
    const action = wrapper.find('[data-testid="feedback-configure"]');
    expect(action.attributes('text')).toBe('Feedback instellen');

    await action.trigger('click');
    expect(wrapper.emitted('configure')).toEqual([['enabled']]);
  });

  it('tells anyone else who can switch it on, without a button they cannot use', () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, enabled: false, canAdmin: false });

    expect(wrapper.find('[data-testid="feedback-empty-off"]').attributes('supporting-text')).toBe(
      'Een beheerder van deze site kan feedback aanzetten.',
    );
    expect(wrapper.find('[data-testid="feedback-configure"]').exists()).toBe(false);
  });

  it('says that nothing came in yet while feedback is on', () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, enabled: true });

    expect(wrapper.find('[data-testid="feedback-empty-none"]').attributes('text')).toBe('Nog geen feedback');
    expect(wrapper.find('[data-testid="feedback-empty-off"]').exists()).toBe(false);
  });

  it('keeps the list of a site with feedback, also once feedback is off', () => {
    const wrapper = makeWrapper({ enabled: false });

    expect(wrapper.find('[data-testid="feedback-list"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-empty-off"]').exists()).toBe(false);
  });

  it('says no page has the button yet, and takes an admin to the live switch', async () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, noButton: 'previews' });

    const empty = wrapper.find('[data-testid="feedback-empty-nowhere"]');
    expect(empty.attributes('text')).toBe('Nog geen pagina heeft de feedbackknop');
    expect(empty.attributes('supporting-text')).toBe(
      'Deze site heeft geen previews, en op de live site staat feedback uit. Zet feedback ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
    );
    expect(wrapper.find('[data-testid="feedback-empty-none"]').exists()).toBe(false);
    const action = wrapper.find('[data-testid="feedback-configure-live"]');
    expect(action.attributes('text')).toBe('Feedback instellen');

    await action.trigger('click');
    expect(wrapper.emitted('configure')).toEqual([['live']]);
  });

  it('says a site without a live version needs one first, with no switch to go to', () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, noButton: 'publish' });

    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').attributes('supporting-text')).toBe(
      'Er staat nog geen versie live en deze site heeft geen previews. Publiceer eerst een versie van de site.',
    );
    expect(wrapper.find('[data-testid="feedback-configure-live"]').exists()).toBe(false);
  });

  it('tells anyone else why no page has the button, without a way out they cannot take', () => {
    const wrapper = makeWrapper({ items: [], counts: NONE, noButton: 'publish', canAdmin: false });

    expect(wrapper.find('[data-testid="feedback-empty-nowhere"]').attributes('supporting-text')).toBe(
      'Er staat nog geen versie live en deze site heeft geen previews.',
    );
    expect(makeWrapper({ items: [], counts: NONE, noButton: 'previews', canAdmin: false }).find(
      '[data-testid="feedback-configure-live"]',
    ).exists()).toBe(false);
  });

  it('says feedback is off rather than that no page has the button, and keeps the list of a site with feedback', () => {
    const off = makeWrapper({ items: [], counts: NONE, enabled: false, noButton: 'previews' });
    expect(off.find('[data-testid="feedback-empty-off"]').exists()).toBe(true);
    expect(off.find('[data-testid="feedback-empty-nowhere"]').exists()).toBe(false);

    const some = makeWrapper({ noButton: 'previews' });
    expect(some.find('[data-testid="feedback-list"]').exists()).toBe(true);
    expect(some.find('[data-testid="feedback-empty-nowhere"]').exists()).toBe(false);
  });

  it('says when the filter leaves nothing, and keeps the filters to change it', () => {
    const wrapper = makeWrapper({ items: [] });

    expect(wrapper.find('[data-testid="feedback-empty-filter"]').attributes('text')).toBe(
      'Geen feedback bij deze keuze',
    );
    expect(wrapper.find('[data-testid="feedback-status"]').exists()).toBe(true);
  });
});

describe('FeedbackInbox: a list that did not come back', () => {
  const REFUSED = new ApiError({ type: 'about:blank', title: 'Serverfout', status: 503, detail: 'Even niet beschikbaar.' });

  it('says so in place of the rows, keeps the filters, and offers to try again', () => {
    const wrapper = makeWrapper({ failed: REFUSED, previewRefs: ['pr-42'], nextCursor: 'fb-2' });

    const failure = wrapper.find('[data-testid="feedback-list-failed"]');
    expect(failure.attributes('variant')).toBe('alert');
    expect(failure.attributes('text')).toBe('Feedback laden lukte niet');
    expect(failure.attributes('supporting-text')).toBe('Even niet beschikbaar.');
    expect(failure.find('[data-testid="feedback-retry"]').attributes('text')).toBe('Opnieuw proberen');
    expect(wrapper.find('[data-testid="feedback-list"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="feedback-status"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="feedback-where"]').exists()).toBe(true);
    // Its cursor belongs to the list before.
    expect(wrapper.find('[data-testid="feedback-more"]').exists()).toBe(false);
  });

  it('says it in a sentence of its own when the network failed', () => {
    const wrapper = makeWrapper({ failed: new TypeError('network down') });

    expect(wrapper.find('[data-testid="feedback-list-failed"]').attributes('supporting-text')).toBe(
      'Probeer het opnieuw.',
    );
  });

  it('puts the focus on the chosen status before it asks again', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = makeWrapper({ failed: REFUSED, status: 'resolved' });

    await wrapper.find('[data-testid="feedback-retry"]').trigger('click');

    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-status-resolved"]').element);
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });

  it('puts the focus on the heading before it asks again, when there are no filters', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = makeWrapper({ failed: REFUSED, items: [], counts: { new: 0, seen: 0, resolved: 0 } });

    await wrapper.find('[data-testid="feedback-retry"]').trigger('click');

    const heading = wrapper.find('#heading-feedback-inbox');
    expect(focus.mock.contexts.at(-1)).toBe(heading.element);
    expect(heading.attributes('tabindex')).toBe('-1');
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });
});

describe('FeedbackInbox: more', () => {
  it('offers more only while there is a next page, and says when it is loading', async () => {
    expect(makeWrapper({ nextCursor: null }).find('[data-testid="feedback-more"]').exists()).toBe(false);

    const wrapper = makeWrapper({ nextCursor: 'fb-2', moreBusy: true });
    const more = wrapper.find('[data-testid="feedback-more"]');
    expect(more.attributes('text')).toBe('Meer laden');
    expect(more.attributes('loading')).toBeDefined();

    await more.trigger('click');
    expect(wrapper.emitted('more')).toHaveLength(1);

    await wrapper.setProps({ moreBusy: false });
    expect(wrapper.find('[data-testid="feedback-more"]').attributes('loading')).toBeUndefined();
  });
});

describe('FeedbackInbox: focus', () => {
  it('puts the focus on the row of an item, or on the first row when it is gone', () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = makeWrapper();

    wrapper.vm.focusItem('fb-2');
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-fb-2"]').element);

    wrapper.vm.focusItem('fb-weg');
    expect(focus.mock.contexts.at(-1)).toBe(wrapper.find('[data-testid="feedback-fb-1"]').element);
    expect(wrapper.find('#heading-feedback-inbox').attributes('tabindex')).toBeUndefined();
  });

  it.each([
    ['the filter leaves nothing', COUNTS],
    ['the site has no feedback left, and no filters either', { new: 0, seen: 0, resolved: 0 }],
  ])('puts the focus on the heading of the list when %s', (_name, counts) => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const wrapper = makeWrapper({ items: [], counts });

    wrapper.vm.focusItem('fb-1');

    const heading = wrapper.find('#heading-feedback-inbox');
    expect(heading.element.tagName).toBe('H2');
    expect(focus.mock.contexts.at(-1)).toBe(heading.element);
    // Focusable from a script, without becoming a stop for Tab.
    expect(heading.attributes('tabindex')).toBe('-1');
  });
});

describe('FeedbackInbox: in English', () => {
  it('names the filters and the sender in English', () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper({ previewRefs: ['pr-42'] });

    expect(wrapper.find('[data-testid="feedback-status-resolved"]').attributes('text')).toBe('Resolved (1)');
    expect(wrapper.find('[data-testid="feedback-where"] option').text()).toBe('Live site and previews');
    expect(wrapper.find('[data-testid="feedback-fb-1"] [slot="supporting-text"]').text()).toContain('By Jan');
  });

  it('says in English why no page has the button, and that the list did not come back', () => {
    _setLocaleForTest('en');
    const none = { new: 0, seen: 0, resolved: 0 };

    const nowhere = makeWrapper({ items: [], counts: none, noButton: 'previews' });
    expect(nowhere.find('[data-testid="feedback-empty-nowhere"]').attributes('text')).toBe(
      'No page has the feedback button yet',
    );
    expect(nowhere.find('[data-testid="feedback-empty-nowhere"]').attributes('supporting-text')).toBe(
      'This site has no previews, and feedback is off for the live site. Turn feedback on for the live site too, or publish a preview with the CLI or from your repository.',
    );
    const publish = makeWrapper({ items: [], counts: none, noButton: 'publish' });
    expect(publish.find('[data-testid="feedback-empty-nowhere"]').attributes('supporting-text')).toBe(
      'No version is live yet and this site has no previews. Publish a version of the site first.',
    );

    const failed = makeWrapper({ failed: new TypeError('network down') }).find('[data-testid="feedback-list-failed"]');
    expect(failed.attributes('text')).toBe('Could not load the feedback');
    expect(failed.attributes('supporting-text')).toBe('Try again.');
    expect(failed.find('[data-testid="feedback-retry"]').attributes('text')).toBe('Try again');
  });

  it('offers the places in English, also without a preview left', () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper({ previewRefs: [] });

    const select = wrapper.find('[data-testid="feedback-where"]');
    expect(select.attributes('aria-label')).toBe('Given on');
    expect(select.findAll('option').map((option) => option.text())).toEqual([
      'Live site and previews',
      'The live site',
      'All previews',
    ]);
  });

  it('names the statuses without the counts in English while the list shows one place', () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper({ previewRefs: ['pr-42'], where: 'preview:pr-42' });

    expect(statusLabels(wrapper)).toEqual(['Open', 'Resolved', 'All']);
  });
});
