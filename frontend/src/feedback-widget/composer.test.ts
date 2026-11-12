import { afterEach, describe, expect, it, vi } from 'vitest';

import { Composer, type ComposerOptions } from './composer';
import { translator } from './i18n';
import { SCREENSHOT_LIMIT, Shot } from './screenshot';
import { fakeConfig, fakePlatform } from './testPlatform';
import { MAX_COMMENT } from './text';
import type { Capture, Config, Kind } from './types';

const t = translator('nl');

afterEach(() => {
  document.body.innerHTML = '';
});

function setUp(
  options: {
    config?: Partial<Config>;
    kind?: Kind;
    quote?: string | null;
    quoteLang?: string | null;
    draft?: { comment: string; name: string };
    opaque?: boolean;
  } = {},
) {
  const platform = fakePlatform();
  let composer: Composer;
  const shot = new Shot(platform, document.createElement('div'), () => composer.renderShot(), SCREENSHOT_LIMIT);
  const calls = {
    announce: vi.fn(),
    take: vi.fn(),
    send: vi.fn(),
    cancel: vi.fn(),
    region: vi.fn(),
    cover: vi.fn(),
    copy: vi.fn().mockResolvedValue('Je tekst is gekopieerd.'),
    withoutScreenshot: vi.fn(),
  };
  const composerOptions: ComposerOptions = {
    t,
    config: fakeConfig(options.config),
    kind: options.kind ?? 'point',
    quote: options.quote ?? null,
    quoteLang: options.quoteLang ?? null,
    shot,
    draft: options.draft ?? { comment: '', name: '' },
    opaque: options.opaque ?? false,
    ...calls,
  };
  composer = new Composer(composerOptions);
  document.body.append(composer.element);
  const find = (key: string) => composer.element.querySelector<HTMLElement>(`[data-key="${key}"]`);
  return { composer, shot, platform, calls, find };
}

function type(field: HTMLTextAreaElement | HTMLInputElement, value: string): void {
  field.value = value;
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('what the composer shows', () => {
  it.each<[Kind, string]>([
    ['point', 'Je feedback gaat over de plek die je aanwees.'],
    ['text', 'Je feedback gaat over deze tekst:'],
    ['page', 'Je feedback gaat over deze hele pagina.'],
  ])('says what a %s remark is about', (kind, text) => {
    const { composer } = setUp({ kind, quote: kind === 'text' ? 'de knop' : null });
    expect(composer.element.querySelector('p')!.textContent).toBe(text);
  });

  it('shows the quote, shortened when it is long', () => {
    expect(setUp({ kind: 'text', quote: 'de knop' }).composer.element.querySelector('.quote')!.textContent).toBe('de knop');
    const long = setUp({ kind: 'text', quote: 'x'.repeat(400) }).composer.element.querySelector('.quote')!;
    expect(long.textContent).toBe(`${'x'.repeat(280)}...`);
    expect(setUp({ kind: 'page' }).composer.element.querySelector('.quote')).toBeNull();
  });

  it('counts the quote in characters, an emoji or a styled letter being one', () => {
    // 150 characters of two UTF-16 units each: all of it shows, without dots.
    const bold = '\u{1D401}\u{1D41E}\u{1D425}'.repeat(50);
    const whole = setUp({ kind: 'text', quote: bold }).composer.element.querySelector('.quote')!;
    expect(whole.textContent).toBe(bold);
    const emoji = '\u{1F600}'.repeat(300);
    const cut = setUp({ kind: 'text', quote: emoji }).composer.element.querySelector('.quote')!;
    expect(cut.textContent).toBe(`${'\u{1F600}'.repeat(280)}...`);
    // 280 characters exactly, wider than 280 units: whole.
    const edge = `${'a'.repeat(270)}${'\u{1F600}'.repeat(10)}`;
    expect(setUp({ kind: 'text', quote: edge }).composer.element.querySelector('.quote')!.textContent).toBe(edge);
  });

  it('starts from the draft and labels the text field with its limit', () => {
    const { composer } = setUp({ draft: { comment: 'Eerder getypt', name: 'Jan' } });
    expect(composer.comment.value).toBe('Eerder getypt');
    expect(composer.name!.value).toBe('Jan');
    expect(composer.element.querySelector('label[for="tf-comment"]')!.textContent).toBe('Je feedback');
    expect(composer.comment.getAttribute('aria-describedby')).toBe('tf-comment-hint tf-comment-error');
    expect(composer.element.querySelector('#tf-comment-hint')!.textContent).toBe('Maximaal 5000 tekens.');
    expect(composer.element.querySelector('.counter')!.textContent).toBe('13 van 5000');
  });

  it('announces the counter only at 90% and at 100%, once each', () => {
    const { composer, calls } = setUp();
    type(composer.comment, 'a'.repeat(4499));
    expect(calls.announce).not.toHaveBeenCalled();
    type(composer.comment, 'a'.repeat(4500));
    expect(calls.announce).toHaveBeenLastCalledWith('Je kunt nog 500 tekens typen.');
    type(composer.comment, 'a'.repeat(4600));
    expect(calls.announce).toHaveBeenCalledOnce();
    type(composer.comment, 'a'.repeat(MAX_COMMENT));
    expect(calls.announce).toHaveBeenLastCalledWith('Je hebt het maximum van 5000 tekens bereikt.');
    type(composer.comment, 'a'.repeat(MAX_COMMENT + 3));
    expect(calls.announce).toHaveBeenCalledTimes(2);
    type(composer.comment, 'a');
    type(composer.comment, 'a'.repeat(MAX_COMMENT));
    expect(calls.announce).toHaveBeenCalledTimes(3);
  });

  it('tells a giver with an account where it said what the site team sees, and claims nothing it cannot know', () => {
    const { composer } = setUp({ config: { account: true, identity: 'account' } });
    expect(composer.name).toBeNull();
    // The configuration holds no name or email address: the consent page,
    // which showed this visitor's own, is what the line points back to.
    expect(composer.element.textContent).toContain(
      'Je stuurt dit met je account. Wat het siteteam van je ziet, stond op de pagina waar je toestemming gaf.',
    );
    expect(composer.element.textContent).not.toMatch(/naam|e-mailadres/);
  });

  it('asks for a name where the site takes feedback with a name', () => {
    const { composer } = setUp({ config: { identity: 'name' } });
    expect(composer.element.querySelector('label[for="tf-name"]')!.textContent).toBe('Je naam');
    expect(composer.name!.getAttribute('aria-required')).toBe('true');
    expect(composer.name!.getAttribute('aria-describedby')).toBe('tf-name-error');
    expect(composer.element.querySelector('#tf-anonymous-note')).toBeNull();
  });

  it('makes the name optional where feedback without a name is allowed, and says what that means', () => {
    const { composer } = setUp({ config: { identity: 'anonymous' } });
    expect(composer.element.querySelector('label[for="tf-name"]')!.textContent).toBe('Je naam (niet verplicht)');
    expect(composer.name!.hasAttribute('aria-required')).toBe(false);
    expect(composer.name!.getAttribute('aria-describedby')).toBe('tf-name-error tf-anonymous-note');
    expect(composer.element.querySelector('#tf-anonymous-note')!.textContent).toBe(t('composer.anonymous.note'));
  });

  it('puts the notice under Versturen for everyone, with the privacy statement in a new tab', () => {
    const { composer, find } = setUp({ config: { account: false, retentionDays: 90 } });
    const notice = composer.element.querySelector('#tf-notice')!;
    expect(notice.textContent).toBe(
      'Iedereen met een rol op deze site in Toon ziet je feedback, ook wie alleen meekijkt. ' +
        'Afgehandelde feedback wordt na 90 dagen verwijderd. ' +
        'Je kunt deze feedback later niet terugzien of verwijderen. Privacyverklaring (opent in een nieuw tabblad)',
    );
    const link = notice.querySelector('a')!;
    expect([link.getAttribute('href'), link.getAttribute('target'), link.getAttribute('rel')]).toEqual([
      'https://beheer.toon.test/-/privacy',
      '_blank',
      'noopener noreferrer',
    ]);
    expect(find('composer.send')!.getAttribute('aria-describedby')).toBe('tf-notice');
    expect(composer.element.querySelector('#tf-privacy-note')).toBeNull();
  });

  it('opens the privacy statement in this tab on a shielded page, and says unsent text is lost then', () => {
    // A new tab from an opaque origin inherits the sandbox, and the privacy
    // statement does not load in one.
    const { composer } = setUp({ opaque: true });
    const link = composer.element.querySelector<HTMLAnchorElement>('#tf-notice a')!;
    expect([link.getAttribute('href'), link.hasAttribute('target'), link.getAttribute('rel')]).toEqual([
      'https://beheer.toon.test/-/privacy',
      false,
      'noreferrer',
    ]);
    expect(link.textContent).toBe('Privacyverklaring');
    const note = composer.element.querySelector(`#${link.getAttribute('aria-describedby')}`)!;
    expect(note.textContent).toBe(
      'De privacyverklaring opent in dit tabblad. Tekst die je nog niet hebt verstuurd, gaat dan verloren.',
    );
    expect(note.getAttribute('class')).toBe('notice');
  });

  it.each([
    [1, 'Afgehandelde feedback wordt na 1 dag verwijderd.'],
    [0, 'Afgehandelde feedback wordt niet vanzelf verwijderd.'],
  ])('says how long resolved feedback stays for %s days', (retentionDays, sentence) => {
    const notice = setUp({ config: { account: true, retentionDays } }).composer.element.querySelector('#tf-notice')!;
    expect(notice.textContent).toContain(sentence);
    expect(notice.textContent).not.toContain('later niet terugzien');
  });

  it('sends and cancels through its buttons', () => {
    const { calls, find } = setUp();
    find('composer.send')!.click();
    find('composer.cancel')!.click();
    expect(calls.send).toHaveBeenCalledOnce();
    expect(calls.cancel).toHaveBeenCalledOnce();
  });
});

describe('checking before sending', () => {
  it.each([
    ['', 'Schrijf eerst je feedback.'],
    ['a'.repeat(MAX_COMMENT + 1), 'Je feedback is te lang. Maximaal 5000 tekens.'],
    ['a\u0007b', t('composer.comment.chars')],
  ])('refuses the comment %#, marks it and puts focus on it', (comment, message) => {
    const { composer } = setUp({ config: { identity: 'name' }, draft: { comment, name: 'Jan' } });
    expect(composer.validate()).toBeNull();
    expect(composer.comment.getAttribute('aria-invalid')).toBe('true');
    expect(composer.element.querySelector('#tf-comment-error')!.textContent).toBe(message);
    expect(document.activeElement).toBe(composer.comment);
  });

  it.each([
    ['', 'Vul je naam in.'],
    [' \u200d ', 'Vul je naam in.'],
    ['n'.repeat(101), 'Je naam is te lang. Maximaal 100 tekens.'],
    ['Jan\u0007Jansen', t('composer.name.chars')],
  ])('refuses the name %#, and puts focus on it when the comment is fine', (name, message) => {
    const { composer } = setUp({ config: { identity: 'name' }, draft: { comment: 'Goed', name } });
    expect(composer.validate()).toBeNull();
    expect(composer.name!.getAttribute('aria-invalid')).toBe('true');
    expect(composer.element.querySelector('#tf-name-error')!.textContent).toBe(message);
    expect(document.activeElement).toBe(composer.name);
  });

  it('puts focus on the first wrong field and clears old errors on the next try', () => {
    const { composer } = setUp({ config: { identity: 'name' } });
    expect(composer.validate()).toBeNull();
    expect(document.activeElement).toBe(composer.comment);
    type(composer.comment, ' Klopt dit? ');
    type(composer.name!, ' Jan ');
    expect(composer.validate()).toEqual({ comment: 'Klopt dit?', author: { kind: 'name', name: 'Jan' } });
    expect(composer.comment.hasAttribute('aria-invalid')).toBe(false);
    expect(composer.element.querySelector('#tf-name-error')!.textContent).toBe('');
  });

  it('sends without a name where that is allowed, and with one when it is given', () => {
    const { composer } = setUp({ config: { identity: 'anonymous' }, draft: { comment: 'x', name: ' \u200b ' } });
    expect(composer.validate()).toEqual({ comment: 'x', author: { kind: 'anonymous' } });
    type(composer.name!, 'Jan');
    expect(composer.validate()!.author).toEqual({ kind: 'name', name: 'Jan' });
    type(composer.name!, 'J\u0000');
    expect(composer.validate()).toBeNull();
    // A joiner alone is no name either, and one in a name stays in it.
    type(composer.name!, ' \u200d ');
    expect(composer.validate()!.author).toEqual({ kind: 'anonymous' });
    type(composer.name!, 'Ma\u200cryam');
    expect(composer.validate()!.author).toEqual({ kind: 'name', name: 'Ma\u200cryam' });
  });

  it('sends with the account when the giver has one', () => {
    const { composer } = setUp({ config: { account: true }, draft: { comment: 'x', name: '' } });
    expect(composer.validate()).toEqual({ comment: 'x', author: { kind: 'account' } });
  });
});

describe('the screenshot block', () => {
  const CAPTURE: Capture = { image: 'c', width: 800, height: 600, scale: 1, placeholders: false };

  it('offers to take one for a remark on the whole page', () => {
    const { calls, find } = setUp({ kind: 'page' });
    find('shot.make')!.click();
    expect(calls.take).toHaveBeenCalledOnce();
  });

  it('says it is being made, then shows it with its warning and the ways to change it', async () => {
    const { composer, shot, platform, calls, find } = setUp({ kind: 'point' });
    platform.capture.mockResolvedValue(CAPTURE);
    const taking = shot.take([]);
    expect(composer.element.querySelector('.block p')!.textContent).toBe('Schermafbeelding wordt gemaakt.');
    expect(calls.announce).toHaveBeenLastCalledWith('Schermafbeelding wordt gemaakt.');
    expect(composer.element.textContent).not.toContain(t('shot.updating'));
    await taking;
    expect(calls.announce).toHaveBeenLastCalledWith('Schermafbeelding klaar.');
    const image = composer.element.querySelector('img')!;
    expect([image.getAttribute('src'), image.getAttribute('alt')]).toEqual([
      'blob:screenshot',
      'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd',
    ]);
    expect(composer.element.querySelector('.block')!.textContent).toContain(t('shot.notice'));
    expect(composer.element.querySelector('.block')!.textContent).not.toContain(t('shot.placeholders'));
    expect(find('shot.uncover')).toBeNull();
    find('shot.region')!.click();
    find('shot.cover')!.click();
    expect(calls.region).toHaveBeenCalledOnce();
    expect(calls.cover).toHaveBeenCalledOnce();
  });

  it.each<[Kind, string]>([
    ['text', 'Schermafbeelding van wat je nu ziet, met de geselecteerde tekst gemarkeerd'],
    ['page', 'Schermafbeelding van wat je nu ziet'],
  ])('describes a %s screenshot', async (kind, alt) => {
    const { composer, shot } = setUp({ kind, quote: kind === 'text' ? 'q' : null });
    await shot.take([]);
    expect(composer.element.querySelector('img')!.getAttribute('alt')).toBe(alt);
  });

  it('says when images show as grey areas, and offers to take the covering away', async () => {
    const { composer, shot, platform, find } = setUp();
    platform.capture.mockResolvedValue({ ...CAPTURE, placeholders: true });
    await shot.take([]);
    expect(composer.element.querySelector('.block')!.textContent).toContain(t('shot.placeholders'));
    await shot.cover({ x: 0, y: 0, width: 20, height: 20 });
    find('shot.uncover')!.click();
    await vi.waitFor(() => expect(shot.covers).toEqual([]));
  });

  it('can go without the screenshot and back', async () => {
    const { composer, shot, find, calls } = setUp();
    await shot.take([]);
    find('shot.without')!.click();
    expect(shot.state).toBe('excluded');
    expect(composer.element.querySelector('.block p')!.textContent).toBe('Je verstuurt dit zonder schermafbeelding.');
    const announced = calls.announce.mock.calls.length;
    find('shot.include')!.click();
    await vi.waitFor(() => expect(shot.state).toBe('ready'));
    expect(calls.announce.mock.calls.length).toBe(announced);
  });

  it('offers another part or none when it is too large', async () => {
    const { composer, shot, platform, find } = setUp();
    platform.encode.mockResolvedValue(new Blob([new Uint8Array(3 * 1024 * 1024)]));
    await shot.take([]);
    expect(composer.element.querySelector('.block p')!.textContent).toBe(t('shot.tooLarge'));
    expect(find('shot.region')).not.toBeNull();
    expect(find('shot.without')).not.toBeNull();
  });

  it('offers to go without while it is being made', () => {
    const { shot, platform, find } = setUp();
    platform.capture.mockReturnValue(new Promise(() => {}));
    void shot.take([]);
    find('shot.without')!.click();
    expect(shot.state).toBe('excluded');
  });

  it('shows the earlier picture as being updated while a cover or a part is drawn, without saying so aloud', async () => {
    const { composer, shot, platform, calls, find } = setUp();
    await shot.take([]);
    let finish!: (blob: Blob) => void;
    platform.encode.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    find('shot.cover')!.focus();
    const announced = calls.announce.mock.calls.length;
    const covering = shot.cover({ x: 0, y: 0, width: 20, height: 20 });
    const block = composer.element.querySelector('.block')!;
    expect(block.textContent).toContain('De schermafbeelding wordt bijgewerkt.');
    expect(block.querySelector('img.thumb')).not.toBeNull();
    expect(document.activeElement).toBe(find('shot.cover'));
    finish(new Blob(['covered']));
    await covering;
    expect(block.textContent).not.toContain(t('shot.updating'));
    expect(calls.announce.mock.calls.length).toBe(announced);
  });

  it('shows a part being drawn when the screenshot was too large, and the screenshot coming back', async () => {
    const { composer, shot, platform } = setUp();
    platform.encode.mockResolvedValue(new Blob([new Uint8Array(3 * 1024 * 1024)]));
    await shot.take([]);
    platform.encode.mockReturnValueOnce(new Promise(() => {}));
    void shot.choose({ x: 0, y: 0, width: 20, height: 20 });
    const block = composer.element.querySelector('.block')!;
    expect(block.textContent).toContain(t('shot.tooLarge'));
    expect(block.textContent).toContain(t('shot.updating'));
    shot.exclude();
    platform.encode.mockReturnValueOnce(new Promise(() => {}));
    void shot.include();
    expect(block.textContent).toContain(t('shot.excluded'));
    expect(block.textContent).toContain(t('shot.updating'));
  });

  it('leaves the screenshot as it is while the feedback is on its way', async () => {
    const { composer, shot, platform, calls, find } = setUp();
    await shot.take([]);
    await shot.cover({ x: 0, y: 0, width: 20, height: 20 });
    composer.setBusy(true);
    for (const key of ['shot.region', 'shot.cover', 'shot.uncover', 'shot.without']) find(key)!.click();
    expect(calls.region).not.toHaveBeenCalled();
    expect(calls.cover).not.toHaveBeenCalled();
    expect([shot.state, shot.covers.length, shot.rendering]).toEqual(['ready', 1, false]);
    composer.setBusy(false);
    find('shot.without')!.click();
    expect(shot.state).toBe('excluded');
    composer.setBusy(true);
    find('shot.include')!.click();
    expect(shot.state).toBe('excluded');
    expect(platform.draw).toHaveBeenCalledTimes(2);
  });

  it('offers to try again when it failed', async () => {
    const { composer, shot, platform, calls, find } = setUp();
    platform.capture.mockRejectedValue(new Error('no'));
    await shot.take([]);
    expect(composer.element.querySelector('.block p')!.textContent).toBe(t('shot.failed'));
    find('shot.retry')!.click();
    expect(calls.take).toHaveBeenCalledOnce();
  });

  describe('says aloud that Versturen would go without it', () => {
    const said = (calls: ReturnType<typeof setUp>['calls']) => calls.announce.mock.calls.map(([message]) => message);

    it('when it failed, each time it fails', async () => {
      const { shot, platform, calls } = setUp();
      platform.capture.mockRejectedValue(new Error('no'));
      await shot.take([]);
      await shot.take([]);
      expect(said(calls)).toEqual([t('shot.making'), t('shot.failed'), t('shot.making'), t('shot.failed')]);
    });

    it('when a cover drawn anew fails', async () => {
      const { shot, platform, calls } = setUp();
      await shot.take([]);
      platform.draw.mockImplementationOnce(() => {
        throw new Error('no');
      });
      await shot.cover({ x: 0, y: 0, width: 20, height: 20 });
      expect(said(calls)).toEqual([t('shot.making'), t('shot.ready'), t('shot.failed')]);
    });

    it('when it came out too large, and once a cover or a part drawn anew ends there, not while it is drawn', async () => {
      const { shot, platform, calls } = setUp();
      const large = new Blob([new Uint8Array(3 * 1024 * 1024)]);
      platform.encode.mockResolvedValue(large);
      await shot.take([]);
      expect(said(calls)).toEqual([t('shot.making'), t('shot.tooLarge')]);
      // A part that is still too large: said again once it is drawn, also
      // when another part was chosen while the first one was drawn.
      let finish!: (blob: Blob) => void;
      platform.encode.mockReturnValueOnce(new Promise(() => {}));
      void shot.choose({ x: 0, y: 0, width: 500, height: 400 });
      platform.encode.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
      const choosing = shot.choose({ x: 0, y: 0, width: 400, height: 300 });
      expect(said(calls)).toHaveLength(2);
      finish(large);
      await choosing;
      expect(said(calls)).toEqual([t('shot.making'), t('shot.tooLarge'), t('shot.tooLarge')]);
      // A smaller part fits; a cover on it then does not.
      platform.encode.mockResolvedValue(new Blob(['jpeg']));
      await shot.choose({ x: 0, y: 0, width: 40, height: 30 });
      platform.encode.mockResolvedValue(large);
      await shot.cover({ x: 0, y: 0, width: 20, height: 20 });
      expect(said(calls).slice(2)).toEqual([t('shot.tooLarge'), t('shot.tooLarge')]);
    });
  });

  it('keeps focus on the same control when the block is drawn anew', async () => {
    const { shot, find } = setUp();
    await shot.take([]);
    find('shot.cover')!.focus();
    await shot.cover({ x: 0, y: 0, width: 20, height: 20 });
    expect(document.activeElement).toBe(find('shot.cover'));
  });

  it('moves focus to the block itself when the focused control is gone', async () => {
    const { shot, find, composer } = setUp();
    await shot.take([]);
    find('shot.without')!.focus();
    shot.exclude();
    expect(document.activeElement).toBe(find('shot.include'));
    const pending = shot.include();
    await pending;
    expect(document.activeElement).toBe(find('shot.region'));
    find('shot.region')!.focus();
    void shot.take([]);
    const heading = composer.element.querySelector<HTMLElement>('.block h3')!;
    expect(document.activeElement).toBe(heading);
    await vi.waitFor(() => expect(shot.state).toBe('ready'));
    expect(document.activeElement).toBe(find('shot.region'));
  });

  it('leaves focus alone when it was elsewhere', async () => {
    const { shot, composer } = setUp();
    composer.comment.focus();
    await shot.take([]);
    expect(document.activeElement).toBe(composer.comment);
  });

  it('lets go of the screenshot when it closes', async () => {
    const { composer, shot, platform } = setUp();
    await shot.take([]);
    composer.dispose();
    expect(platform.revokeObjectUrl).toHaveBeenCalledWith('blob:screenshot');
  });
});

describe('when sending fails', () => {
  const CAPTURE_FOR_ALERT: Capture = { image: 'c', width: 800, height: 600, scale: 1, placeholders: false };

  it('shows the reason with the remedies that fit, and clears it again', async () => {
    const { composer, calls, find } = setUp();
    composer.showError({ message: 'Versturen lukt niet meer.', copy: true, withoutScreenshot: true }, true);
    expect(composer.element.querySelector('[role="alert"]')!.textContent).toBe('Versturen lukt niet meer.');
    find('error.copy')!.click();
    expect(calls.copy).toHaveBeenCalledWith(composer.comment);
    // What the copy did is shown in the row, under the remedies.
    const row = find('error.copy')!.parentElement!;
    await vi.waitFor(() => expect(row.lastElementChild!.textContent).toBe('Je tekst is gekopieerd.'));
    find('error.withoutShot')!.click();
    expect(calls.withoutScreenshot).toHaveBeenCalledOnce();

    composer.showError({ message: 'Te groot.', copy: false, withoutScreenshot: true }, false);
    expect(find('error.copy')).toBeNull();
    expect(find('error.withoutShot')).toBeNull();

    composer.showError(null, false);
    expect(composer.element.querySelector('[role="alert"]')!.textContent).toBe('');
  });

  it('moves focus from a remedy that goes to Versturen, and leaves focus elsewhere alone', () => {
    const { composer, find } = setUp();
    composer.showError({ message: 'Te groot.', copy: false, withoutScreenshot: true }, true);
    find('error.withoutShot')!.focus();
    composer.showError(null, false);
    expect(document.activeElement).toBe(find('composer.send'));

    composer.showError({ message: 'Te groot.', copy: false, withoutScreenshot: true }, true);
    composer.comment.focus();
    composer.showError(null, false);
    expect(document.activeElement).toBe(composer.comment);
  });

  it('says why the screenshot holds Versturen back, until the screenshot changes', async () => {
    const { composer, shot, platform, find, calls } = setUp();
    let capture!: (value: typeof CAPTURE_FOR_ALERT) => void;
    platform.capture.mockReturnValueOnce(new Promise((resolve) => (capture = resolve)));
    void shot.take([]);
    composer.shotProblem(t('error.shot.notReady'));
    const alert = composer.element.querySelector('[role="alert"]')!;
    expect(alert.textContent).toBe(t('error.shot.notReady'));
    find('error.withoutShot')!.click();
    expect(calls.withoutScreenshot).toHaveBeenCalledOnce();
    find('error.withoutShot')!.focus();
    capture(CAPTURE_FOR_ALERT);
    await vi.waitFor(() => expect(shot.state).toBe('ready'));
    expect(alert.textContent).toBe('');
    expect(find('error.withoutShot')).toBeNull();
    // Not on Versturen, where the next Enter would send the picture that
    // arrived while the giver was about to leave it out.
    expect(document.activeElement).toBe(composer.element.querySelector('.block h3'));
  });

  it('keeps focus where it was when the screenshot arrives while focus is outside the alert', async () => {
    const { composer, shot, platform } = setUp();
    let capture!: (value: typeof CAPTURE_FOR_ALERT) => void;
    platform.capture.mockReturnValueOnce(new Promise((resolve) => (capture = resolve)));
    void shot.take([]);
    composer.shotProblem(t('error.shot.notReady'));
    composer.comment.focus();
    capture(CAPTURE_FOR_ALERT);
    await vi.waitFor(() => expect(shot.state).toBe('ready'));
    expect(composer.element.querySelector('[role="alert"]')!.textContent).toBe('');
    expect(document.activeElement).toBe(composer.comment);
  });

  it('keeps that alert through a redraw that leaves the screenshot as it was', async () => {
    const { composer, shot, platform } = setUp();
    await shot.take([]);
    platform.encode.mockReturnValueOnce(new Promise(() => {}));
    composer.shotProblem(t('error.shot.changed'));
    void shot.cover({ x: 0, y: 0, width: 10, height: 10 });
    expect(composer.element.querySelector('[role="alert"]')!.textContent).toBe(t('error.shot.changed'));
    composer.showError(null, false);
    shot.exclude();
    expect(composer.element.querySelector('[role="alert"]')!.textContent).toBe('');
  });

  it('shows progress while busy', () => {
    const { composer } = setUp();
    composer.setBusy(true);
    expect(composer.element.textContent).toContain('Je feedback wordt verstuurd.');
    composer.setBusy(false);
    expect(composer.element.textContent).not.toContain('Je feedback wordt verstuurd.');
  });
});
