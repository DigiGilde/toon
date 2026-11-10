/**
 * The words the design system says by itself.
 *
 * `@nldd/design-system` carries its own Dutch for the text it renders without
 * being told: the picker on a combo box, the label of a footer navigation, the
 * button that closes a notification. There is no way to set a language for the
 * package as a whole, only a `translations` property per component, so this
 * section holds those strings on our side and `designSystem.ts` hands them to
 * the elements.
 *
 * Every Dutch value here is the package's own default, character for character.
 * That is what keeps the Dutch interface exactly as it was, and
 * `tests/design-system-text.test.ts` checks it against the package on every
 * run: a word the design system rewrites is a word we have to look at again
 * rather than silently keep saying.
 *
 * The exceptions are `designSystem.comboBox.openMenu` and
 * `designSystem.fileField.showOptions`: the package says "Toon opties", which
 * would read as the name of the product. The test lists both as reworded on
 * purpose.
 */
export const designSystemNl = {
  'designSystem.activityIndicator.loading': 'Laden',
  'designSystem.banner.dismiss': 'Verberg',
  'designSystem.breadcrumbs.label': 'Kruimelpad',
  'designSystem.codeViewer.region': 'Code',
  'designSystem.codeViewer.copy': 'Kopieer',
  'designSystem.codeViewer.copied': 'Gekopieerd',
  'designSystem.codeViewer.copyFailed': 'Kopiëren mislukt',
  'designSystem.comboBox.openMenu': 'Opties tonen',
  'designSystem.comboBox.clear': 'Wis invoer',
  'designSystem.fileField.choose': 'Bestand kiezen',
  'designSystem.fileField.change': 'Wijzigen',
  'designSystem.fileField.chosen': '{name} gekozen',
  'designSystem.fileField.clear': 'Wis selectie',
  'designSystem.fileField.required': 'Een bestand is verplicht',
  'designSystem.fileField.dropHint': 'of sleep het hierheen',
  'designSystem.fileField.dragHere': 'Sleep het bestand hierheen',
  'designSystem.fileField.dropHere': 'Zet het bestand hier neer',
  'designSystem.fileField.replaceDrag': 'Sleep hierheen om te vervangen',
  'designSystem.fileField.replaceDrop': 'Zet hier neer om te vervangen',
  'designSystem.fileField.typeError': 'Alleen {types}',
  'designSystem.fileField.wrongType': 'Bestandstype past niet',
  'designSystem.fileField.typeList': '{items} of {last}',
  'designSystem.fileField.tooMany': 'Sleep één bestand',
  'designSystem.fileField.folder': 'Sleep bestand, geen map',
  'designSystem.fileField.chooseFilesOrFolder': 'Bestanden of map kiezen',
  'designSystem.fileField.chooseFilesMenu': 'Bestanden kiezen…',
  'designSystem.fileField.chooseFolderMenu': 'Map kiezen…',
  'designSystem.fileField.chooseFolder': 'Map kiezen',
  'designSystem.fileField.dismissMessage': 'sluit melding',
  'designSystem.fileField.emptyFolder': 'Map is leeg',
  'designSystem.fileField.unreadableFolder': 'Map niet te lezen',
  'designSystem.fileField.folderCount': '{folder} ({count} bestanden)',
  'designSystem.fileField.folderOneFile': '{folder} (1 bestand)',
  'designSystem.fileField.folderCountShort': '{folder} ({count})',
  'designSystem.fileField.showOptions': 'Opties tonen',
  'designSystem.linkHint.newTab': 'opent in nieuw tabblad',
  'designSystem.linkHint.external': 'andere website',
  'designSystem.linkHint.externalNewTab': 'andere website, opent in nieuw tabblad',
  'designSystem.list.arrowKeys': 'Gebruik de pijltjestoetsen om door de lijst te navigeren.',
  'designSystem.menu.oneResult': '1 resultaat',
  'designSystem.menu.results': '{count} resultaten',
  'designSystem.notification.dismiss': 'Sluit',
  'designSystem.notification.region': 'Meldingen',
  'designSystem.pageFooter.legalLinks': 'Juridische links',
  'designSystem.progressBar.completedSuffix': 'voltooid',
  'designSystem.progressBar.totalPrefix': 'Totaal',
  'designSystem.progressBar.loading': 'Aan het laden',
  'designSystem.progressBar.label': 'Voortgang',
  'designSystem.searchField.search': 'Zoek',
  'designSystem.searchField.clear': 'Wis zoekopdracht',
  'designSystem.splitButton.showOptions': 'Meer opties voor {text}',
  'designSystem.validationList.email': 'Een geldig e-mailadres zoals naam@voorbeeld.nl.',
  'designSystem.validationList.url': 'Een geldig webadres zoals https://www.voorbeeld.nl.',
  'designSystem.validationList.valueMissing': 'Dit veld is verplicht.',
  'designSystem.validationList.tooShort': 'Minstens {minlength} tekens.',
  'designSystem.validationList.tooLong': 'Hoogstens {maxlength} tekens.',
  'designSystem.validationList.rangeUnderflow': '{min} of meer.',
  'designSystem.validationList.rangeOverflow': '{max} of minder.',
  'designSystem.validationList.invalid': 'Dit klopt nog niet.',
  'designSystem.tabBar.overflow': 'Meer tabs',
  'designSystem.toolbar.overflow': 'Meer',
} as const;
