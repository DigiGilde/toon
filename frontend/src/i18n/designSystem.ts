/**
 * Puts the interface language into the design system's own components.
 *
 * `@nldd/design-system` renders text of its own that we never write: the
 * picker on a combo box, the label of a footer navigation, the button that
 * closes a notification. Its default is Dutch. In an English interface that
 * would be read out with English phonetics, since the page declares
 * `lang="en"` (WCAG 3.1.2), so the English admin gets the package's own US
 * English set.
 *
 * `setTranslations()` sets the words for the whole package, which also reaches
 * the components another component builds in its shadow root, such as the
 * activity indicator in a loading button.
 */
import { setTranslations } from '@nldd/design-system/translations';
import { enUS } from '@nldd/design-system/translations/en-US';
import { watch } from 'vue';

import { currentLocale, t } from './index';

/**
 * Where Toon says something other than the package: its Dutch "Toon opties"
 * and the like would read as the name of the product. Every such text is here,
 * also for components this app does not use yet, so a new one is never seen
 * first on a screen. The English is the package's own.
 */
export function ownWords(): Record<string, string> {
  return {
    'components.collection.load-more-action': t('designSystem.collection.loadMore'),
    'components.combo-box.open-menu-action': t('designSystem.comboBox.openMenu'),
    'components.document-tab-bar.show-more-tabs-action': t(
      'designSystem.documentTabBar.showMoreTabs',
    ),
    'components.file-field.show-options-action': t('designSystem.fileField.showOptions'),
    'components.password-field.show-action': t('designSystem.passwordField.show'),
    'components.password-field.show-accessible-label': t('designSystem.passwordField.showLabel'),
    'components.token.open-menu-action': t('designSystem.token.openMenu'),
    'components.token.open-token-menu-action': t('designSystem.token.openTokenMenu'),
    'components.token-field.open-menu-action': t('designSystem.tokenField.openMenu'),
    'components.token-field.open-token-menu-action': t('designSystem.tokenField.openTokenMenu'),
  };
}

/** Everything handed to the package for the language on screen. */
export function packageWords(): Record<string, string> {
  return { ...(currentLocale.value === 'en' ? enUS : {}), ...ownWords() };
}

/**
 * The words of the upload field, which takes a whole folder too: the
 * package's speak of one file while something is dragged. Set on that element
 * alone, since every other file field here takes one file.
 */
export function folderFieldWords(): Record<string, string> {
  return {
    'components.file-field.drag-here-text': t('publish.upload.field.dragHere'),
    'components.file-field.drop-here-text': t('publish.upload.field.dropHere'),
  };
}

/**
 * Hand the words to the package, and again whenever the language changes.
 * Returns the way to stop, which only the tests need.
 */
export function useDesignSystemText(): () => void {
  setTranslations(packageWords());
  return watch(currentLocale, () => setTranslations(packageWords()));
}
