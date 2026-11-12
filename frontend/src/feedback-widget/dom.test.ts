import { describe, expect, it, vi } from 'vitest';

import { append, button, copyControl, h, replace, speechBubbleIcon, tabbable, withWords } from './dom';

describe('building elements', () => {
  it('sets attributes, skipping empty ones, and adds text as text', () => {
    const element = h('p', { id: 'a', hidden: true, 'aria-describedby': undefined, title: null, draggable: false, tabindex: -1 }, [
      '<b>niet vet</b>',
      null,
      false,
      undefined,
      h('span', {}, ['x']),
    ]);
    expect(element.outerHTML).toBe('<p id="a" hidden="" tabindex="-1">&lt;b&gt;niet vet&lt;/b&gt;<span>x</span></p>');
  });

  it('puts words of the page in a message, in a span with their language where it is another', () => {
    // As written: a replacement pattern in the page's words stays text.
    expect(withWords('Gemarkeerd: {description}', { text: 'Prijs $& meer', lang: null })).toBe('Gemarkeerd: Prijs $& meer');
    const parts = withWords('Marked: {description}.', { text: 'Jierferslach', lang: 'fy' }) as Array<string | HTMLElement>;
    const holder = h('p', {}, parts);
    expect(holder.innerHTML).toBe('Marked: <span lang="fy">Jierferslach</span>.');
  });

  it('builds with defaults', () => {
    expect(h('div').outerHTML).toBe('<div></div>');
  });

  it('appends and replaces children', () => {
    const parent = h('div', {}, ['a']);
    append(parent, ['b']);
    expect(parent.textContent).toBe('ab');
    replace(parent, ['c', h('i')]);
    expect(parent.innerHTML).toBe('c<i></i>');
  });

  it('makes a labelled button that answers a click', () => {
    const onPress = vi.fn();
    const plain = button('Sluiten', onPress);
    expect(plain.outerHTML).toBe('<button type="button" class="btn">Sluiten</button>');
    plain.click();
    expect(onPress).toHaveBeenCalledOnce();

    const full = button('Versturen', onPress, {
      key: 'composer.send',
      primary: true,
      describedBy: 'tf-notice',
      className: 'extra',
    });
    expect(full.getAttribute('class')).toBe('btn btn-primary extra');
    expect(full.dataset.key).toBe('composer.send');
    expect(full.getAttribute('aria-describedby')).toBe('tf-notice');
  });

  it('makes a copy button with a line beside it that shows how the copy went', async () => {
    const copy = vi.fn().mockResolvedValueOnce('Je tekst is gekopieerd.').mockResolvedValueOnce('Kopiëren lukte niet.');
    const [control, notice] = copyControl('Kopieer je tekst', copy, 'error.copy');
    expect([control.textContent, control.dataset.key, notice.textContent]).toEqual(['Kopieer je tekst', 'error.copy', '']);
    // Not a live region: the status region says it already.
    expect(notice.hasAttribute('role')).toBe(false);
    control.click();
    await vi.waitFor(() => expect(notice.textContent).toBe('Je tekst is gekopieerd.'));
    control.click();
    await vi.waitFor(() => expect(notice.textContent).toBe('Kopiëren lukte niet.'));
  });

  it('lists what Tab reaches, in order, leaving out what is hidden or not a stop', () => {
    const root = h('div', {}, [
      h('h2', { tabindex: '-1' }, ['Kop']),
      h('button', { id: 'one' }),
      h('a', { id: 'two', href: '#x' }),
      h('a', {}, ['no href']),
      h('form', { hidden: true }, [h('input', { type: 'text' })]),
      h('input', { id: 'three', type: 'text' }),
      h('input', { type: 'hidden' }),
      h('textarea', { id: 'four' }),
      h('select', { id: 'five' }),
      h('div', { id: 'six', tabindex: '0' }),
    ]);
    expect(tabbable(root).map((element) => element.id)).toEqual(['one', 'two', 'three', 'four', 'five', 'six']);
  });

  it('draws the icon as hidden SVG', () => {
    const icon = speechBubbleIcon();
    expect(icon.namespaceURI).toBe('http://www.w3.org/2000/svg');
    expect(icon.getAttribute('aria-hidden')).toBe('true');
    expect(icon.querySelector('path')).not.toBeNull();
  });
});
