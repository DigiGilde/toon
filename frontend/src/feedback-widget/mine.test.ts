import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiFailure, type MineItem, type MineReply } from './api';
import { translator } from './i18n';
import { MineView } from './mine';
import { STYLES } from './styles';
import { MAX_COMMENT } from './text';

const t = translator('nl');

afterEach(() => {
  document.body.innerHTML = '';
});

function item(overrides: Partial<MineItem> = {}): MineItem {
  return {
    id: 'i1',
    kind: 'point',
    status: 'new',
    comment: 'De knop valt weg',
    quote: null,
    anchor: null,
    page: { path: 'docs/', title: 'Documentatie', url: '/team/site/docs/' },
    createdAt: '2026-11-03T13:30:00Z',
    replies: [],
    replyCount: 0,
    ...overrides,
  };
}

const REPLY: MineReply = { id: 'r9', author: 'you', name: null, body: 'Bedankt', createdAt: '2026-11-04T08:00:00Z' };

function setUp(drafts = new Map<string, string>(), fields = new Map<string, HTMLTextAreaElement>()) {
  const calls = {
    announce: vi.fn(),
    reply: vi.fn().mockResolvedValue(REPLY),
    remove: vi.fn().mockResolvedValue(undefined),
    copy: vi.fn().mockResolvedValue('Je tekst is gekopieerd.'),
    removed: vi.fn(),
    back: vi.fn(),
  };
  const view = new MineView({ t, lang: 'nl', drafts, fields, ...calls });
  document.body.append(view.element);
  const find = (key: string) => view.element.querySelector<HTMLElement>(`[data-key="${key}"]`);
  return { view, calls, find, drafts, fields };
}

function type(field: HTMLTextAreaElement, value: string): void {
  field.value = value;
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

function problem(code: string): ApiFailure {
  return new ApiFailure({ type: 'problem', status: 404, code, detail: null, retryAfter: null });
}

describe('Mijn feedback', () => {
  it('says it is loading, and goes back with its button', () => {
    const { view, calls, find } = setUp();
    expect(view.element.textContent).toContain('Je feedback wordt opgehaald.');
    find('mine.back')!.click();
    expect(calls.back).toHaveBeenCalledOnce();
  });

  it('says so when there is nothing yet', () => {
    const { view } = setUp();
    view.show([], new Set(), false);
    expect(view.element.textContent).toContain('Je hebt op deze site nog geen feedback gegeven met je account.');
  });

  it('shows an error instead of the list', () => {
    const { view } = setUp();
    view.showError('Dit lukt niet meer vanaf deze pagina. Laad de pagina opnieuw.');
    expect(view.element.querySelector('[role="alert"]')!.textContent).toBe(
      'Dit lukt niet meer vanaf deze pagina. Laad de pagina opnieuw.',
    );
    expect(view.element.textContent).not.toContain('opgehaald');
  });

  it('lists each item with its status, text, quote, page and replies, all as text', () => {
    const { view } = setUp();
    view.show(
      [
        item({
          kind: 'text',
          status: 'resolved',
          comment: '<img src=x onerror=alert(1)>\nTweede regel',
          quote: 'de knop',
          replies: [
            { id: 'r1', author: 'team', name: 'Jan Jansen', body: 'Opgelost', createdAt: '2026-11-04T08:00:00Z' },
            { id: 'r2', author: 'team', name: null, body: 'Nog iets', createdAt: 'never' },
            REPLY,
          ],
          replyCount: 3,
        }),
        item({ id: 'i2', page: { path: '', title: null, url: null }, createdAt: 'never' }),
      ],
      new Set(['i2']),
      false,
    );
    const [first, second] = Array.from(view.element.querySelectorAll<HTMLElement>('li.item'));
    expect(first!.querySelector('.meta')!.textContent).toMatch(/^Afgehandeld, Tekst, 3 nov\.? 2026/);
    expect(first!.querySelector('img')).toBeNull();
    expect(first!.querySelector('.multiline')!.textContent).toBe('<img src=x onerror=alert(1)>\nTweede regel');
    expect(first!.querySelector('.quote')!.textContent).toBe('de knop');
    const link = first!.querySelector('a')!;
    expect([link.textContent, link.getAttribute('href')]).toEqual(['Documentatie', '/team/site/docs/']);
    const replies = Array.from(first!.querySelectorAll('.replies li'), (reply) => reply.textContent);
    expect(replies[0]).toMatch(/^Siteteam \(Jan Jansen\), 4 nov/);
    expect(replies[1]).toBe('SiteteamNog iets');
    expect(replies[2]).toMatch(/^Jij, 4 nov.*Bedankt$/);
    expect(first!.textContent).not.toContain(t('mine.lost'));
    expect(first!.textContent).not.toContain(t('mine.olderReplies'));

    expect(second!.querySelector('.meta')!.textContent).toBe('Nieuw, Punt');
    expect(second!.querySelector('a')).toBeNull();
    expect(second!.textContent).toContain('Pagina: /');
    expect(second!.textContent).toContain('Deze plek is op de pagina niet meer te vinden.');
  });

  it('breaks a long page title, or the path in its place, anywhere rather than scroll the panel sideways', () => {
    const { view } = setUp();
    const path = 'werkinstructies/2026/aanvraagformulier_subsidieregeling_duurzame_energie_bijlage.html';
    view.show(
      [
        item({ page: { path: 'docs/', title: 'Arbeidsongeschiktheidsverzekeringsvoorwaardenoverzicht', url: '/x/' } }),
        item({ id: 'i2', page: { path, title: null, url: `/team/site/${path}` } }),
      ],
      new Set(),
      false,
    );
    const lines = Array.from(view.element.querySelectorAll('li.item a'), (link) => link.parentElement!);
    expect(lines.map((line) => [line.className, line.textContent])).toEqual([
      ['url', 'Pagina: Arbeidsongeschiktheidsverzekeringsvoorwaardenoverzicht'],
      ['url', `Pagina: /${path}`],
    ]);
    expect(STYLES).toContain('.url { overflow-wrap: anywhere; }');
  });

  it('says older replies are not in the list when more replies exist than came along', () => {
    const { view } = setUp();
    const latest = Array.from({ length: 10 }, (_, index) => ({ ...REPLY, id: `r${index}`, body: `Reactie ${index}` }));
    view.show([item({ replies: latest, replyCount: 25 })], new Set(), false);
    const element = view.element.querySelector('li.item')!;
    const note = Array.from(element.querySelectorAll('p')).find((p) => p.textContent === t('mine.olderReplies'))!;
    // Where the older ones would be: right before the replies, oldest first.
    expect(note.nextElementSibling).toBe(element.querySelector('.replies'));
    expect(element.querySelectorAll('.replies li').length).toBe(10);
  });

  it('says older feedback is not in the list when the answer left items out, below the newest', () => {
    const { view } = setUp();
    view.show([item(), item({ id: 'i2' })], new Set(), true);
    const note = view.element.querySelector('.list')!.nextElementSibling!;
    expect(note.textContent).toBe('Oudere feedback staat niet in deze lijst.');
    view.show([item()], new Set(), false);
    expect(view.element.textContent).not.toContain(t('mine.olderItems'));
  });

  it('does not call a list empty when it only left older items out', () => {
    const { view } = setUp();
    view.show([], new Set(), true);
    expect(view.element.textContent).toContain(t('mine.olderItems'));
    expect(view.element.textContent).not.toContain(t('mine.empty'));
  });

  it('says again which places are gone after the page changed its layout', () => {
    const { view } = setUp();
    view.show([item(), item({ id: 'i2' })], new Set(['i1']), false);
    const text = (id: string) => view.element.querySelector(`[data-item="${id}"]`)!.textContent;
    expect([text('i1'), text('i2')].map((content) => content!.includes(t('mine.lost')))).toEqual([true, false]);
    view.markLost(new Set(['i2']));
    expect([text('i1'), text('i2')].map((content) => content!.includes(t('mine.lost')))).toEqual([false, true]);
  });

  it('puts focus on an item a pin points at', () => {
    const { view } = setUp();
    view.show([item()], new Set(), false);
    view.focusItem('i1');
    expect(document.activeElement).toBe(view.element.querySelector('[data-item="i1"]'));
    view.focusItem('gone');
  });
});

describe('replying', () => {
  it('refuses an empty, too long or unsendable reply, marking the field', async () => {
    const { view, calls, find } = setUp();
    view.show([item()], new Set(), false);
    const field = view.element.querySelector('textarea')!;
    const error = view.element.querySelector('.error')!;
    for (const [text, message] of [
      ['  ', 'Schrijf eerst je reactie.'],
      ['a'.repeat(MAX_COMMENT + 1), 'Je reactie is te lang. Maximaal 5000 tekens.'],
      ['a\u0001', t('mine.reply.chars')],
    ]) {
      field.value = text!;
      find('reply-i1')!.click();
      await vi.waitFor(() => expect(error.textContent).toBe(message));
      expect(field.getAttribute('aria-invalid')).toBe('true');
      expect(document.activeElement).toBe(field);
    }
    expect(calls.reply).not.toHaveBeenCalled();
  });

  it('sends a reply, shows it under the others and empties the field', async () => {
    const { view, calls, find } = setUp();
    view.show([item()], new Set(), false);
    const field = view.element.querySelector('textarea')!;
    field.value = '  Bedankt  ';
    find('reply-i1')!.click();
    await vi.waitFor(() => expect(calls.announce).toHaveBeenCalledWith('Je reactie is verstuurd.'));
    expect(calls.reply).toHaveBeenCalledWith('i1', 'Bedankt');
    expect(view.element.querySelector('.replies')!.textContent).toContain('Bedankt');
    expect(field.value).toBe('');
    expect(field.hasAttribute('aria-invalid')).toBe(false);
  });

  it('keeps what is typed as a draft for the item, and fills the field from it when the list is drawn anew', () => {
    const { view, drafts } = setUp();
    view.show([item(), item({ id: 'i2' })], new Set(), false);
    const [first, second] = Array.from(view.element.querySelectorAll('textarea'));
    type(first!, 'Nog een vraag');
    type(second!, 'Weg');
    type(second!, '');
    expect([...drafts]).toEqual([['i1', 'Nog een vraag']]);
    view.show([item(), item({ id: 'i2' })], new Set(), false);
    const fields = Array.from(view.element.querySelectorAll('textarea'), (field) => field.value);
    expect(fields).toEqual(['Nog een vraag', '']);
  });

  it('lets go of the draft of an item that is no longer in the list', () => {
    const { view, drafts } = setUp(new Map([['gone', 'Oud'], ['i1', 'Blijft']]));
    view.show([item()], new Set(), false);
    expect([...drafts]).toEqual([['i1', 'Blijft']]);
    expect(view.element.querySelector('textarea')!.value).toBe('Blijft');
  });

  it('holds no draft while the reply is on its way, none once it is sent, and the reply again when it is refused', async () => {
    const { view, calls, find, drafts } = setUp();
    let finish!: (reply: MineReply) => void;
    calls.reply.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    view.show([item()], new Set(), false);
    const field = view.element.querySelector('textarea')!;
    type(field, 'Bedankt');
    find('reply-i1')!.click();
    expect(drafts.has('i1')).toBe(false);
    finish(REPLY);
    await vi.waitFor(() => expect(field.value).toBe(''));
    expect(drafts.has('i1')).toBe(false);

    calls.reply.mockRejectedValueOnce(new ApiFailure({ type: 'network' }));
    type(field, 'Nog een keer');
    find('reply-i1')!.click();
    await vi.waitFor(() => expect(drafts.get('i1')).toBe('Nog een keer'));
  });

  it('ignores a second press while the first reply is on its way', async () => {
    const { view, calls, find } = setUp();
    let finish!: (reply: MineReply) => void;
    calls.reply.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    view.show([item()], new Set(), false);
    view.element.querySelector('textarea')!.value = 'Een';
    find('reply-i1')!.click();
    find('reply-i1')!.click();
    expect(calls.reply).toHaveBeenCalledOnce();
    finish(REPLY);
    await vi.waitFor(() => expect(calls.announce).toHaveBeenCalled());
  });

  describe('text typed while the reply is on its way', () => {
    function sending(text: string) {
      const mounted = setUp();
      let finish!: (reply: MineReply) => void;
      mounted.calls.reply.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
      mounted.view.show([item()], new Set(), false);
      const field = mounted.view.element.querySelector('textarea')!;
      type(field, text);
      mounted.find('reply-i1')!.click();
      return { ...mounted, field, finish };
    }

    it('stays in the field once the reply is answered: only what was sent goes', async () => {
      const { calls, drafts, field, finish } = sending('Dank je, we kijken ernaar.');
      type(field, 'Dank je, we kijken ernaar. En nog een vraag: welke pagina bedoel je?');
      finish(REPLY);
      await vi.waitFor(() => expect(calls.announce).toHaveBeenCalledWith('Je reactie is verstuurd.'));
      expect(calls.reply).toHaveBeenCalledWith('i1', 'Dank je, we kijken ernaar.');
      expect(field.value).toBe('En nog een vraag: welke pagina bedoel je?');
      expect(drafts.get('i1')).toBe('En nog een vraag: welke pagina bedoel je?');
    });

    it('stays as it is when what was sent was changed meanwhile', async () => {
      const { calls, drafts, field, finish } = sending('Eerste versie');
      type(field, 'Tweede versie');
      finish(REPLY);
      await vi.waitFor(() => expect(calls.announce).toHaveBeenCalledWith('Je reactie is verstuurd.'));
      expect(field.value).toBe('Tweede versie');
      expect(drafts.get('i1')).toBe('Tweede versie');
    });

    it('waits without what was sent in a list the giver left', async () => {
      const { view, calls, drafts, field, finish } = sending('Dank je.');
      type(field, 'Dank je. Nog iets.');
      view.element.remove();
      finish(REPLY);
      await vi.waitFor(() => expect(calls.announce).toHaveBeenCalledWith('Je reactie is verstuurd.'));
      expect(drafts.get('i1')).toBe('Nog iets.');
    });
  });

  describe('answered after the list was drawn anew', () => {
    function drawnAnew() {
      const first = setUp();
      let settle!: { resolve: (reply: MineReply) => void; reject: (error: unknown) => void };
      first.calls.reply.mockReturnValueOnce(new Promise((resolve, reject) => (settle = { resolve, reject })));
      first.view.show([item()], new Set(), false);
      type(first.view.element.querySelector('textarea')!, 'Antwoord A');
      first.find('reply-i1')!.click();
      // Terug, and Mijn feedback again.
      first.view.element.remove();
      const second = setUp(first.drafts, first.fields);
      second.view.show([item()], new Set(), false);
      const field = second.view.element.querySelector('textarea')!;
      return { first, second, field, settle, drafts: first.drafts };
    }

    it('leaves the draft typed in the newer list alone when it goes through', async () => {
      const { first, field, settle, drafts } = drawnAnew();
      expect(field.value).toBe('');
      type(field, 'Antwoord B');
      settle.resolve(REPLY);
      await vi.waitFor(() => expect(first.calls.announce).toHaveBeenCalledWith('Je reactie is verstuurd.'));
      expect([drafts.get('i1'), field.value]).toEqual(['Antwoord B', 'Antwoord B']);
    });

    it('leaves the draft typed in the newer list alone when it is refused, and does not say the text waits', async () => {
      const { first, field, settle, drafts } = drawnAnew();
      type(field, 'Antwoord B');
      settle.reject(
        new ApiFailure({ type: 'problem', status: 429, code: 'FEEDBACK_RATE_LIMITED', detail: null, retryAfter: 30 }),
      );
      await vi.waitFor(() =>
        expect(first.calls.announce).toHaveBeenCalledWith(
          'Je reactie is niet geplaatst. Er komt nu veel feedback binnen. Probeer het over 30 seconden opnieuw.',
        ),
      );
      expect([drafts.get('i1'), field.value]).toEqual(['Antwoord B', 'Antwoord B']);
    });

    it('keeps the refused text for the next time the list opens when nothing was typed for the item since', async () => {
      const { first, settle, drafts } = drawnAnew();
      settle.reject(new ApiFailure({ type: 'network' }));
      await vi.waitFor(() =>
        expect(first.calls.announce).toHaveBeenCalledWith(
          'Je reactie is niet geplaatst. Dat lukte niet. Probeer het later opnieuw. ' +
            'Open Mijn feedback opnieuw, dan staat je tekst er weer.',
        ),
      );
      expect(drafts.get('i1')).toBe('Antwoord A');
      const third = setUp(drafts, first.fields);
      third.view.show([item()], new Set(), false);
      expect(third.view.element.querySelector('textarea')!.value).toBe('Antwoord A');
    });

    it('forgets the field of an item the newer list no longer holds', () => {
      const { first, second } = drawnAnew();
      expect(first.fields.has('i1')).toBe(true);
      second.view.show([item({ id: 'i2' })], new Set(), false);
      expect([...first.fields.keys()]).toEqual(['i2']);
    });
  });

  it('keeps the text and offers to copy it when the page can no longer reply', async () => {
    const { view, calls, find } = setUp();
    calls.reply.mockRejectedValue(problem('FEEDBACK_UNAVAILABLE'));
    view.show([item()], new Set(), false);
    const field = view.element.querySelector('textarea')!;
    field.value = 'Mijn reactie';
    find('reply-i1')!.click();
    await vi.waitFor(() => expect(find('copy-i1')).not.toBeNull());
    expect(view.element.querySelector('li.item [role="alert"]')!.textContent).toBe(t('error.unavailable'));
    expect(field.value).toBe('Mijn reactie');
    find('copy-i1')!.click();
    expect(calls.copy).toHaveBeenCalledWith(field);
    // What happened is shown beside the button, not only said.
    await vi.waitFor(() => expect(find('copy-i1')!.nextElementSibling!.textContent).toBe('Je tekst is gekopieerd.'));
  });

  it('says a thread that is full takes no more replies, and keeps the reply', async () => {
    const { view, calls, find } = setUp();
    calls.reply.mockRejectedValue(new ApiFailure({ type: 'problem', status: 409, code: 'FEEDBACK_THREAD_FULL', detail: null, retryAfter: null }));
    view.show([item()], new Set(), false);
    const field = view.element.querySelector('textarea')!;
    field.value = 'Nog een vraag';
    find('reply-i1')!.click();
    await vi.waitFor(() =>
      expect(view.element.querySelector('li.item [role="alert"]')!.textContent).toBe(t('error.threadFull')),
    );
    expect(field.value).toBe('Nog een vraag');
    expect(find('copy-i1')).toBeNull();
  });

  describe('refused after the giver left the list', () => {
    function leftWhileSending(failure: unknown) {
      const mounted = setUp();
      let fail!: (error: unknown) => void;
      mounted.calls.reply.mockReturnValueOnce(new Promise((_resolve, reject) => (fail = reject)));
      mounted.view.show([item()], new Set(), false);
      mounted.view.element.querySelector('textarea')!.value = 'Mijn reactie';
      mounted.find('reply-i1')!.click();
      mounted.view.element.remove();
      fail(failure);
      return mounted;
    }

    it('says so through the status region, with the reason and where the text waits', async () => {
      const { calls, drafts } = leftWhileSending(new ApiFailure({ type: 'network' }));
      await vi.waitFor(() =>
        expect(calls.announce).toHaveBeenCalledWith(
          'Je reactie is niet geplaatst. Dat lukte niet. Probeer het later opnieuw. ' +
            'Open Mijn feedback opnieuw, dan staat je tekst er weer.',
        ),
      );
      expect(drafts.get('i1')).toBe('Mijn reactie');
    });

    it('says to reload, not to copy, when the page can no longer reply: no field is left to copy from', async () => {
      const { calls } = leftWhileSending(problem('FEEDBACK_UNAVAILABLE'));
      await vi.waitFor(() =>
        expect(calls.announce).toHaveBeenCalledWith(
          'Je reactie is niet geplaatst. Dit lukt niet meer vanaf deze pagina. Laad de pagina opnieuw.',
        ),
      );
    });

    it('says nothing aloud while the list still shows the refusal', async () => {
      const { view, calls, find } = setUp();
      calls.reply.mockRejectedValue(new ApiFailure({ type: 'network' }));
      view.show([item()], new Set(), false);
      view.element.querySelector('textarea')!.value = 'x';
      find('reply-i1')!.click();
      await vi.waitFor(() =>
        expect(view.element.querySelector('li.item [role="alert"]')!.textContent).toBe(t('error.later')),
      );
      expect(calls.announce).not.toHaveBeenCalled();
    });
  });

  it('says to try later without a copy button for other failures', async () => {
    const { view, calls, find } = setUp();
    calls.reply.mockRejectedValue(new ApiFailure({ type: 'network' }));
    view.show([item()], new Set(), false);
    view.element.querySelector('textarea')!.value = 'x';
    find('reply-i1')!.click();
    await vi.waitFor(() =>
      expect(view.element.querySelector('li.item [role="alert"]')!.textContent).toBe(t('error.later')),
    );
    expect(find('copy-i1')).toBeNull();
  });
});

describe('deleting', () => {
  it('asks inline first, with focus on Annuleren, and Annuleren brings back the buttons', () => {
    const { view, calls, find } = setUp();
    view.show([item()], new Set(), false);
    find('delete-i1')!.click();
    const cancel = find('cancel-i1')!;
    expect(document.activeElement).toBe(cancel);
    const question = view.element.querySelector(`#${cancel.getAttribute('aria-describedby')}`)!;
    expect(question.textContent).toBe(t('mine.delete.question'));
    cancel.click();
    expect(document.activeElement).toBe(find('delete-i1'));
    expect(calls.remove).not.toHaveBeenCalled();
  });

  it('closes an open question with Escape, one step only', () => {
    const { view, find } = setUp();
    view.show([item()], new Set(), false);
    expect(view.escape()).toBe(false);
    find('delete-i1')!.click();
    expect(view.escape()).toBe(true);
    expect(find('cancel-i1')).toBeNull();
    expect(document.activeElement).toBe(find('delete-i1'));
    expect(view.escape()).toBe(false);
  });

  it('keeps one question open at a time', () => {
    const { view, find } = setUp();
    view.show([item(), item({ id: 'i2' })], new Set(), false);
    find('delete-i1')!.click();
    find('delete-i2')!.click();
    expect(find('cancel-i1')).toBeNull();
    expect(document.activeElement).toBe(find('cancel-i2'));
  });

  it('removes the item after the answer, says so and tells the widget', async () => {
    const { view, calls, find } = setUp();
    view.show([item(), item({ id: 'i2' })], new Set(), false);
    find('delete-i1')!.click();
    find('confirm-i1')!.click();
    find('confirm-i1')!.click();
    await vi.waitFor(() => expect(calls.removed).toHaveBeenCalledWith('i1'));
    expect(calls.remove).toHaveBeenCalledOnce();
    expect(view.element.querySelector('[data-item="i1"]')).toBeNull();
    expect(calls.announce).toHaveBeenCalledWith('Je feedback is verwijderd.');
    expect(view.escape()).toBe(false);
  });

  it('says the list is empty after the last one goes', async () => {
    const { view, calls, find } = setUp();
    view.show([item()], new Set(), false);
    find('delete-i1')!.click();
    find('confirm-i1')!.click();
    await vi.waitFor(() => expect(calls.removed).toHaveBeenCalled());
    expect(view.element.textContent).toContain(t('mine.empty'));
  });

  it('does not call the list empty after the last one shown goes, when older items were left out', async () => {
    const { view, calls, find } = setUp();
    view.show([item()], new Set(), true);
    find('delete-i1')!.click();
    find('confirm-i1')!.click();
    await vi.waitFor(() => expect(calls.removed).toHaveBeenCalled());
    expect(view.element.textContent).not.toContain(t('mine.empty'));
    expect(view.element.textContent).toContain(t('mine.olderItems'));
  });

  it('drops the draft of an item that is deleted', async () => {
    const { view, calls, find, drafts } = setUp(new Map([['i1', 'Toch niet']]));
    view.show([item()], new Set(), false);
    find('delete-i1')!.click();
    find('confirm-i1')!.click();
    await vi.waitFor(() => expect(calls.removed).toHaveBeenCalled());
    expect(drafts.size).toBe(0);
  });

  it('keeps the item and says why when deleting fails', async () => {
    const { view, calls, find } = setUp();
    calls.remove.mockRejectedValue(problem('FEEDBACK_UNAVAILABLE'));
    view.show([item()], new Set(), false);
    find('delete-i1')!.click();
    find('confirm-i1')!.click();
    await vi.waitFor(() =>
      expect(view.element.querySelector('li.item [role="alert"]')!.textContent).toBe(t('error.unavailable.other')),
    );
    expect(view.element.querySelector('[data-item="i1"]')).not.toBeNull();
    expect(calls.removed).not.toHaveBeenCalled();
  });
});
