/**
 * The handing of words to the design system. What is checked here is the
 * mechanism; that our own words still hang on the package's keys is
 * `tests/design-system-text.test.ts`.
 */
import { getTranslations } from '@nldd/design-system/translations';
import { enUS } from '@nldd/design-system/translations/en-US';
import { afterEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';

import { _setLocaleForTest } from './index';
import { folderFieldWords, ownWords, useDesignSystemText } from './designSystem';

const OPEN_MENU = 'components.combo-box.open-menu-action';
const BREADCRUMBS = 'components.breadcrumbs.accessible-label';
const DRAG_HERE = 'components.file-field.drag-here-text';

let stop: (() => void) | null = null;

afterEach(() => {
  stop?.();
  stop = null;
  _setLocaleForTest('nl');
});

describe('useDesignSystemText', () => {
  it('leaves Dutch to the package, but for our own words', () => {
    stop = useDesignSystemText();

    expect(getTranslations()).toEqual(ownWords());
    expect(getTranslations()[OPEN_MENU]).toBe('Opties tonen');
    expect(getTranslations()[BREADCRUMBS]).toBeUndefined();
  });

  it('hands the package its own English, with our own words on top', () => {
    _setLocaleForTest('en');
    stop = useDesignSystemText();

    expect(getTranslations()[BREADCRUMBS]).toBe(enUS[BREADCRUMBS]);
    expect(Object.keys(getTranslations())).toHaveLength(Object.keys(enUS).length);
    expect(getTranslations()[OPEN_MENU]).toBe('Show options');
  });

  it('follows a change of language, both ways', async () => {
    stop = useDesignSystemText();
    expect(getTranslations()[BREADCRUMBS]).toBeUndefined();

    _setLocaleForTest('en');
    await nextTick();
    expect(getTranslations()[BREADCRUMBS]).toBe('Breadcrumbs');

    _setLocaleForTest('nl');
    await nextTick();
    expect(getTranslations()[BREADCRUMBS]).toBeUndefined();
  });

  it('stops when it is told to', async () => {
    stop = useDesignSystemText();
    stop();
    stop = null;

    _setLocaleForTest('en');
    await nextTick();

    expect(getTranslations()[BREADCRUMBS]).toBeUndefined();
  });
});

describe('folderFieldWords', () => {
  it('speaks of a folder too, in the language on screen', () => {
    expect(folderFieldWords()[DRAG_HERE]).toBe('Sleep het bestand of de map hierheen');
    _setLocaleForTest('en');
    expect(folderFieldWords()[DRAG_HERE]).toBe('Drag the file or folder here');
  });
});
