/**
 * The design system's own words, held against the package.
 *
 * `src/i18n/designSystem.ts` hands the package its own US English in an
 * English interface, and a few words of ours in either language. Both age with
 * the package: a key gets renamed and the override silently does nothing, a
 * new key arrives without English and the English admin speaks Dutch there, a
 * new Dutch text starts with "Toon" and reads as the name of the product. So
 * this reads the package on every run.
 */
import { enUS } from '@nldd/design-system/translations/en-US';
import { nl } from '@nldd/design-system/translations/nl';
import { afterEach, describe, expect, it } from 'vitest';

import { _setLocaleForTest } from '../src/i18n';
import { folderFieldWords, ownWords } from '../src/i18n/designSystem';

/** The product's name as a word, in any case: "Toon opties", "toon meer". */
const PRODUCT_NAME = /\btoon\b/i;

const packageText = (key: string): string | undefined => nl[key as keyof typeof nl];

describe('the words the design system says by itself', () => {
  afterEach(() => _setLocaleForTest('nl'));

  it('has English for every text the package can say', () => {
    const missing = Object.keys(nl).filter((key) => !(key in enUS));
    expect(missing).toEqual([]);
  });

  it('hangs our own words on keys the package still has', () => {
    const stale = [...Object.keys(ownWords()), ...Object.keys(folderFieldWords())].filter(
      (key) => packageText(key) === undefined,
    );
    expect(stale).toEqual([]);
  });

  it('rewords every Dutch text of the package that reads as the name of the product', () => {
    _setLocaleForTest('nl');
    const own = ownWords();
    const unseen = Object.entries(nl)
      .filter(([key, text]) => PRODUCT_NAME.test(text) && own[key] === undefined)
      .map(([key, text]) => `${key}: "${text}"`);
    expect(unseen).toEqual([]);
    const ours = Object.entries(own)
      .filter(([, text]) => PRODUCT_NAME.test(text))
      .map(([key]) => key);
    expect(ours).toEqual([]);
  });

  it('rewords only what the package still says that way', () => {
    _setLocaleForTest('nl');
    const stale = Object.keys(ownWords()).filter((key) => !PRODUCT_NAME.test(packageText(key) ?? ''));
    expect(stale).toEqual([]);
  });

  it('keeps the package English where it rewords the Dutch', () => {
    _setLocaleForTest('en');
    const apart = Object.entries(ownWords())
      .filter(([key, text]) => enUS[key as keyof typeof enUS] !== text)
      .map(([key, text]) => `${key}: "${text}" against "${enUS[key as keyof typeof enUS]}"`);
    expect(apart).toEqual([]);
  });
});
