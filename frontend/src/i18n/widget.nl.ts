/**
 * Dutch strings of the feedback widget that Toon adds to the pages of a site.
 *
 * Not part of the SPA catalogue: the widget is its own bundle on the content
 * host and must not pull in Vue or the admin's language module. widget.en.ts
 * is typed against this object, so a key in one language and not the other
 * is a compile error.
 */
export const widgetNl = {
  // -- The button and its landmark ---------------------------------------------
  'widget.region': 'Feedback',
  'widget.trigger': 'Feedback',
  'widget.newTab': '(opent in een nieuw tabblad)',

  // -- The menu ------------------------------------------------------------------
  'menu.heading': 'Feedback op deze pagina',
  'menu.login': 'Feedback geven met je account',
  'menu.login.note':
    'Log je in, dan bewaart Toon je SSO-id, e-mailadres en IP-adres tot 90 dagen na je laatste keer inloggen. ' +
    'Mislukt het inloggen, breek je het af of beëindigt de inlogdienst je sessie eerder, ' +
    'dan blijft je IP-adres drie jaar in het toegangslogboek.',
  'menu.point': 'Wijs een punt aan',
  'menu.text': 'Feedback op geselecteerde tekst',
  'menu.page': 'Feedback op deze pagina',
  'menu.mine': 'Mijn feedback',
  'menu.account': 'Met je account versturen',
  'menu.account.note':
    'Je gaat naar een pagina van Toon. Tekst die je nog niet hebt verstuurd, gaat dan verloren.',
  'menu.stopAccount': 'Stoppen met je account',
  'menu.hide': 'Knop verbergen',
  'menu.close': 'Sluiten',
  'menu.noSelection': 'Selecteer eerst tekst op de pagina.',
  'menu.floating': 'Feedback op deze tekst',

  // -- Pointing at a spot ----------------------------------------------------------
  'placing.label': 'Wijs een punt aan',
  'placing.bar': 'Klik of tik op de plek waar je feedback over gaat.',
  'placing.keys': 'Pijltjes verplaatsen het kruis, met Shift sneller. Enter kiest de plek.',
  'placing.under': 'Onder het kruis: {description}',
  'placing.nothing': 'Onder het kruis staat niets met tekst.',
  'placing.done': 'Punt gekozen.',
  'placing.cancelled': 'Gestopt met aanwijzen.',
  'placing.stop': 'Stoppen',

  // -- Choosing a part or covering something ------------------------------------
  'region.label': 'Kies het deel van de schermafbeelding dat je wilt versturen',
  'region.bar': 'Sleep een kader of klik op twee hoeken van het deel dat je wilt versturen.',
  'region.done': 'Deel gekozen.',
  'cover.label': 'Kies wat je wilt afdekken',
  'cover.bar': 'Sleep een kader of klik op twee hoeken van wat je wilt afdekken.',
  'cover.done': 'Afgedekt.',
  'rect.keys': 'Pijltjes verplaatsen het kader, met Shift veranderen ze de maat. Enter bevestigt.',
  'rect.size': 'Kader van {width} bij {height} pixels.',
  'rect.cancelled': 'Gestopt zonder wijziging.',

  // -- Writing the feedback --------------------------------------------------------
  'composer.context.point': 'Je feedback gaat over de plek die je aanwees.',
  'composer.context.text': 'Je feedback gaat over deze tekst:',
  'composer.context.page': 'Je feedback gaat over deze hele pagina.',
  'composer.comment': 'Je feedback',
  'composer.comment.hint': 'Maximaal {max} tekens.',
  'composer.counter': '{count} van {max}',
  'composer.counter.near': 'Je kunt nog {left} tekens typen.',
  'composer.counter.full': 'Je hebt het maximum van {max} tekens bereikt.',
  'composer.comment.empty': 'Schrijf eerst je feedback.',
  'composer.comment.long': 'Je feedback is te lang. Maximaal {max} tekens.',
  'composer.comment.chars':
    'Je feedback bevat een teken dat niet verstuurd kan worden. Haal het weg en probeer het opnieuw.',
  'composer.name.required': 'Je naam',
  'composer.name.optional': 'Je naam (niet verplicht)',
  'composer.name.empty': 'Vul je naam in.',
  'composer.name.long': 'Je naam is te lang. Maximaal {max} tekens.',
  'composer.name.chars': 'Je naam bevat een teken dat niet verstuurd kan worden.',
  'composer.anonymous.note':
    'Zonder naam weet het siteteam niet van wie dit komt. Je tekst of de schermafbeelding kan nog wel iets over je zeggen.',
  'composer.account':
    'Je stuurt dit met je account. Wat het siteteam van je ziet, stond op de pagina waar je toestemming gaf.',
  'composer.notice.visibility':
    'Iedereen met een rol op deze site in Toon ziet je feedback, ook wie alleen meekijkt.',
  'composer.notice.retention': 'Afgehandelde feedback wordt na {days} dagen verwijderd.',
  'composer.notice.retention.one': 'Afgehandelde feedback wordt na 1 dag verwijderd.',
  'composer.notice.retention.keep': 'Afgehandelde feedback wordt niet vanzelf verwijderd.',
  'composer.notice.noAccount': 'Je kunt deze feedback later niet terugzien of verwijderen.',
  'composer.privacy': 'Privacyverklaring',
  'composer.privacy.note':
    'De privacyverklaring opent in dit tabblad. Tekst die je nog niet hebt verstuurd, gaat dan verloren.',
  'composer.send': 'Versturen',
  'composer.cancel': 'Annuleren',
  'composer.sending': 'Je feedback wordt verstuurd.',

  // -- The screenshot --------------------------------------------------------------
  'shot.heading': 'Schermafbeelding',
  'shot.make': 'Schermafbeelding maken',
  'shot.making': 'Schermafbeelding wordt gemaakt.',
  'shot.ready': 'Schermafbeelding klaar.',
  'shot.updating': 'De schermafbeelding wordt bijgewerkt.',
  'shot.alt.point': 'Schermafbeelding van wat je nu ziet, met de aangewezen plek gemarkeerd',
  'shot.alt.text': 'Schermafbeelding van wat je nu ziet, met de geselecteerde tekst gemarkeerd',
  'shot.alt.page': 'Schermafbeelding van wat je nu ziet',
  'shot.notice':
    'De schermafbeelding laat zien wat nu op je scherm staat, ook gegevens van anderen of van jezelf. Controleer hem, dek iets af of verstuur zonder.',
  'shot.placeholders': 'Afbeeldingen op deze pagina staan als grijze vlakken in de schermafbeelding.',
  'shot.region': 'Ander deel kiezen',
  'shot.cover': 'Iets afdekken',
  'shot.uncover': 'Afdekking weghalen',
  'shot.without': 'Zonder schermafbeelding',
  'shot.excluded': 'Je verstuurt dit zonder schermafbeelding.',
  'shot.include': 'Toch met schermafbeelding',
  'shot.failed':
    'De schermafbeelding is niet gelukt. Je kunt je feedback zonder schermafbeelding versturen.',
  'shot.tooLarge':
    'De schermafbeelding is te groot geworden. Kies een kleiner deel of verstuur zonder schermafbeelding.',
  'shot.retry': 'Opnieuw proberen',

  // -- When sending does not work --------------------------------------------------
  'error.unavailable':
    'Versturen lukt niet meer vanaf deze pagina. Kopieer je tekst en laad de pagina opnieuw.',
  'error.unavailable.other': 'Dit lukt niet meer vanaf deze pagina. Laad de pagina opnieuw.',
  'error.account': 'Dit kan alleen met je account. Kopieer je tekst en laad de pagina opnieuw.',
  'error.account.other': 'Dit kan alleen met je account. Laad de pagina opnieuw.',
  'error.author':
    'Deze site neemt feedback op deze manier niet meer aan. Kopieer je tekst en laad de pagina opnieuw.',
  'error.moved':
    'De site of groep heeft een nieuw adres gekregen. Kopieer je tekst en laad de pagina opnieuw.',
  'error.moved.other': 'De site of groep heeft een nieuw adres gekregen. Laad de pagina opnieuw.',
  'error.invalid': 'Er klopt iets niet met je feedback.',
  'error.shot.large': 'De schermafbeelding is te groot voor deze site.',
  'error.shot.invalid': 'Deze schermafbeelding kan niet worden verstuurd.',
  'error.shot.quota': 'Er is geen ruimte meer voor schermafbeeldingen.',
  'error.shot.notReady':
    'De schermafbeelding is nog niet klaar. Wacht even of verstuur je feedback zonder schermafbeelding.',
  'error.shot.changed':
    'Je feedback is niet verstuurd, omdat de schermafbeelding niet kon worden bijgewerkt.',
  'error.full': 'Er staat op deze site al te veel feedback open. Probeer het later opnieuw.',
  'error.rate': 'Er komt nu veel feedback binnen. Probeer het over {time} opnieuw.',
  'error.rate.soon': 'Er komt nu veel feedback binnen. Probeer het zo opnieuw.',
  'error.busy': 'Toon heeft het nu te druk. Probeer het over {time} opnieuw.',
  'error.busy.soon': 'Toon heeft het nu te druk. Probeer het zo opnieuw.',
  'error.timeout': 'Het versturen duurde te lang. Probeer het opnieuw.',
  'error.threadFull': 'Bij deze feedback kun je geen reacties meer plaatsen.',
  'error.later': 'Dat lukte niet. Probeer het later opnieuw.',
  'error.copy': 'Kopieer je tekst',
  'error.copied': 'Je tekst is gekopieerd.',
  'error.copyFailed': 'Kopiëren lukte niet. Je tekst is geselecteerd, kopieer hem zelf.',
  'error.withoutShot': 'Versturen zonder schermafbeelding',
  'time.second': '1 seconde',
  'time.seconds': '{n} seconden',
  'time.minutes': '{n} minuten',

  // -- Sent ----------------------------------------------------------------------------
  'sent.heading': 'Bedankt, je feedback is verstuurd.',
  'sent.account': 'Je vindt die terug onder Mijn feedback.',
  'sent.noAccount': 'Reacties van het siteteam kun je niet zien.',
  'sent.more': 'Meer feedback geven',

  // -- My feedback ---------------------------------------------------------------------
  'mine.heading': 'Mijn feedback',
  'mine.loading': 'Je feedback wordt opgehaald.',
  'mine.empty': 'Je hebt op deze site nog geen feedback gegeven met je account.',
  'mine.back': 'Terug',
  'mine.status.new': 'Nieuw',
  'mine.status.seen': 'Gezien',
  'mine.status.resolved': 'Afgehandeld',
  'mine.kind.page': 'Hele pagina',
  'mine.kind.point': 'Punt',
  'mine.kind.text': 'Tekst',
  'mine.page': 'Pagina:',
  'mine.lost': 'Deze plek is op de pagina niet meer te vinden.',
  'mine.olderReplies': 'Oudere reacties staan niet in deze lijst.',
  'mine.olderItems': 'Oudere feedback staat niet in deze lijst.',
  'mine.team': 'Siteteam',
  'mine.teamNamed': 'Siteteam ({name})',
  'mine.you': 'Jij',
  'mine.reply': 'Je reactie',
  'mine.replySend': 'Reageren',
  'mine.replySent': 'Je reactie is verstuurd.',
  'mine.reply.refused': 'Je reactie is niet geplaatst. {reason}',
  'mine.reply.kept': 'Open Mijn feedback opnieuw, dan staat je tekst er weer.',
  'mine.reply.empty': 'Schrijf eerst je reactie.',
  'mine.reply.long': 'Je reactie is te lang. Maximaal {max} tekens.',
  'mine.reply.chars':
    'Je reactie bevat een teken dat niet verstuurd kan worden. Haal het weg en probeer het opnieuw.',
  'mine.delete': 'Verwijderen',
  'mine.delete.question':
    'Wil je deze feedback verwijderen? Het siteteam ziet die daarna ook niet meer.',
  'mine.deleted': 'Je feedback is verwijderd.',
  'mine.pin': 'Feedback: {words}',

  // -- Hiding the button -----------------------------------------------------------------
  'hide.heading': 'Knop verbergen',
  'hide.minute': 'Een minuut',
  'hide.session': 'Tot je de browser sluit',
  'hide.minute.done': 'De feedbackknop is verborgen en komt over een minuut terug.',
  'hide.back': 'De feedbackknop is weer zichtbaar.',
  'hide.confirm':
    'De knop blijft op deze site weg tot je de browser sluit, en niet langer dan 12 uur. Terugzetten kan via deze link:',
  'hide.confirmButton': 'Verbergen',

  // -- A place shown from a link ---------------------------------------------------------
  'highlight.label': 'Hier gaat de feedback over',
  'highlight.shown': 'De plek waar de feedback over gaat, is gemarkeerd.',
  'highlight.shownAt': 'Gemarkeerd: {description}',
  'highlight.lost': 'De plek van deze feedback is op deze pagina niet meer te vinden.',
} as const;
