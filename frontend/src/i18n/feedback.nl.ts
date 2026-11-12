/**
 * Dutch catalogue section: the Feedback tab of the site page, with the list of
 * what visitors sent, the detail of one item and the settings that decide who
 * may send it.
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const feedbackNl = {
  // -- The list -------------------------------------------------------------
  'feedback.loading': 'Feedback laden',
  'feedback.inbox.heading': 'Ontvangen feedback',
  'feedback.inbox.intro':
    "Wat bezoekers met de feedbackknop over de pagina's van deze site stuurden, nieuwste eerst.",
  'feedback.inbox.status.label': 'Status',
  'feedback.inbox.status.open': 'Open ({count})',
  'feedback.inbox.status.resolved': 'Afgehandeld ({count})',
  'feedback.inbox.status.all': 'Alles ({count})',
  'feedback.inbox.status.open.uncounted': 'Open',
  'feedback.inbox.status.resolved.uncounted': 'Afgehandeld',
  'feedback.inbox.status.all.uncounted': 'Alles',
  'feedback.inbox.where.label': 'Gegeven op',
  'feedback.inbox.where.all': 'Live en previews',
  'feedback.inbox.where.live': 'De live site',
  'feedback.inbox.where.previews': 'Alle previews',
  'feedback.inbox.where.preview': 'Preview {ref}',
  'feedback.inbox.more': 'Meer laden',
  'feedback.inbox.more.failed': 'Meer feedback laden lukte niet',
  'feedback.inbox.more.failed.detail': 'Probeer het opnieuw.',
  'feedback.inbox.failed': 'Feedback laden lukte niet',
  'feedback.inbox.failed.detail': 'Probeer het opnieuw.',

  // -- What the list says when it has nothing to show ------------------------
  'feedback.empty.off': 'Feedback staat uit',
  'feedback.empty.off.admin':
    'Zet feedback aan onder Feedback verzamelen. Dan krijgen de previews van deze site een feedbackknop.',
  'feedback.empty.off.member': 'Een beheerder van deze site kan feedback aanzetten.',
  'feedback.empty.off.action': 'Feedback instellen',
  'feedback.empty.none': 'Nog geen feedback',
  'feedback.empty.none.detail': 'Wat bezoekers met de feedbackknop sturen, komt hier binnen.',
  'feedback.empty.nowhere': 'Nog geen pagina heeft de feedbackknop',
  'feedback.empty.nowhere.previews': 'Deze site heeft geen previews, en op de live site staat feedback uit.',
  'feedback.empty.nowhere.publish': 'Er staat nog geen versie live en deze site heeft geen previews.',
  'feedback.empty.filter': 'Geen feedback bij deze keuze',
  'feedback.empty.filter.detail': 'Kies hierboven een andere status of plek.',

  // -- One item, in the list and in the detail -------------------------------
  'feedback.status.new': 'Nieuw',
  'feedback.status.seen': 'Gezien',
  'feedback.status.resolved': 'Afgehandeld',
  'feedback.kind.page': 'Hele pagina',
  'feedback.kind.point': 'Punt',
  'feedback.kind.text': 'Tekst',
  'feedback.target.live': 'Live',
  'feedback.target.preview': 'Preview {ref}',
  'feedback.page.root': 'Startpagina',
  'feedback.place': '{page} ({target})',
  'feedback.author.named': 'Door {name}',
  'feedback.author.accountUnnamed': 'Door iemand met een account',
  'feedback.author.someone': 'Iemand met een account',
  'feedback.author.unverified': 'Naam niet gecontroleerd',
  'feedback.author.anonymous': 'Zonder naam',
  'feedback.row.replies.one': '1 reactie',
  'feedback.row.replies.many': '{count} reacties',
  'feedback.row.screenshot': 'met schermafbeelding',
  'feedback.time.now': 'zojuist',

  // -- The detail sheet ------------------------------------------------------
  'feedback.sheet.title': 'Feedback op {page}',
  'feedback.sheet.label': 'Feedback op {page}, {date}',
  'feedback.sheet.close': 'Sluiten',
  'feedback.sheet.quote': 'Over deze tekst',
  'feedback.sheet.view': 'Bekijk op de pagina',
  'feedback.sheet.view.gone':
    'Preview {ref} is verlopen of bestaat niet meer, dus deze plek is niet meer te bekijken.',
  'feedback.sheet.view.off': 'Feedback staat uit voor deze site, dus de pagina opent zonder deze plek te markeren.',
  'feedback.sheet.view.offLive':
    'Feedback staat uit op de live site, dus de pagina opent zonder deze plek te markeren.',
  'feedback.sheet.screenshot.alt': 'Schermafbeelding bij deze feedback',
  'feedback.sheet.screenshot.open': 'Schermafbeelding op ware grootte',
  'feedback.meta.label': 'Gegevens bij deze feedback',
  'feedback.meta.author': 'Door',
  'feedback.meta.author.email': '{name}, {email}',
  'feedback.meta.created': 'Op',
  'feedback.meta.kind': 'Soort',
  'feedback.meta.page': 'Pagina',
  'feedback.meta.target': 'Live of preview',
  'feedback.meta.version': 'Versie',
  'feedback.meta.version.live': '{date}, nu live',
  'feedback.meta.version.gone': 'Niet meer beschikbaar',
  'feedback.meta.viewport': 'Venstergrootte',
  'feedback.meta.viewport.value': '{width} bij {height} pixels',
  'feedback.meta.unknown': 'Onbekend',
  'feedback.meta.status': 'Status',
  'feedback.meta.status.changed': '{status}, door {name} op {date}',
  'feedback.meta.status.changedUnnamed': '{status} op {date}',

  // -- The thread and the reply form ------------------------------------------
  'feedback.thread.heading': 'Reacties',
  'feedback.thread.empty': 'Nog geen reacties.',
  'feedback.thread.team': 'Siteteam',
  'feedback.thread.teamNamed': 'Siteteam, {name}',
  'feedback.thread.giver': 'Indiener',
  'feedback.thread.byline': '{author}, {date}',
  'feedback.reply.label': 'Reactie',
  'feedback.reply.help.account':
    'De indiener ziet je reactie, met je naam, in de feedbackknop onder Mijn feedback.',
  'feedback.reply.help.noAccount':
    'Deze feedback kwam zonder account binnen, dus de indiener ziet je reactie niet.',
  'feedback.reply.required': 'Een reactie is nodig',
  'feedback.reply.submit': 'Reageren',
  'feedback.reply.sent': 'Reactie geplaatst',
  'feedback.reply.failed': 'Reageren lukte niet. Probeer het opnieuw.',
  'feedback.reply.failed.other': 'Reactie niet geplaatst bij de feedback op {page}',
  'feedback.reply.failed.kept': 'Als je die feedback weer opent, staat je reactie in het veld.',
  'feedback.reply.failed.restored': 'Je reactie staat weer in het veld.',

  // -- Status and deleting ------------------------------------------------------
  'feedback.action.seen': 'Markeer als gezien',
  'feedback.action.resolve': 'Markeer als afgehandeld',
  'feedback.action.reopen': 'Heropenen',
  'feedback.action.done.new': 'Feedback heropend',
  'feedback.action.done.seen': 'Gemarkeerd als gezien',
  'feedback.action.done.resolved': 'Gemarkeerd als afgehandeld',
  'feedback.action.failed': 'Status niet gewijzigd',
  'feedback.action.failed.detail': 'Opslaan lukte niet. Probeer het opnieuw.',
  'feedback.action.failed.other': 'Status niet gewijzigd bij de feedback op {page}',
  'feedback.readOnly': 'Alleen redacteuren en beheerders kunnen reageren of de status wijzigen.',
  'feedback.delete': 'Verwijderen',
  'feedback.delete.confirm.title': 'Deze feedback verwijderen?',
  'feedback.delete.confirm.text':
    'De feedback verdwijnt met de reacties en de schermafbeelding. Dat kan niet ongedaan worden gemaakt.',
  'feedback.delete.confirm.keep': 'Behouden',
  'feedback.delete.done': 'Feedback verwijderd',
  'feedback.delete.failed': 'Feedback niet verwijderd',
  'feedback.delete.failed.detail': 'Verwijderen lukte niet. Probeer het opnieuw.',

  // -- Settings -------------------------------------------------------------
  'feedback.settings.heading': 'Feedback verzamelen',
  'feedback.settings.intro':
    "Met feedback aan voegt Toon een knop Feedback toe aan de pagina's van deze site. Daarmee wijzen bezoekers een plek of een tekst aan en sturen ze een opmerking, met een schermafbeelding als ze dat willen.",
  'feedback.settings.enabled': 'Feedback toestaan',
  'feedback.settings.enabled.off':
    'Zet je dit aan, dan krijgen de previews van deze site de feedbackknop.',
  'feedback.settings.identity.heading': 'Wie mag feedback geven',
  'feedback.settings.identity.account': 'Alleen met een account',
  'feedback.settings.identity.account.hint':
    'Wie feedback geeft, logt in. Het siteteam ziet de naam, redacteuren en beheerders ook het e-mailadres.',
  'feedback.settings.identity.name': 'Ook met een zelf ingevulde naam',
  'feedback.settings.identity.name.hint': 'Inloggen hoeft dan niet. Toon controleert die naam niet.',
  'feedback.settings.identity.anonymous': 'Ook zonder naam',
  'feedback.settings.identity.anonymous.hint':
    'Iedereen die de pagina kan bekijken, kan feedback geven zonder te zeggen van wie die komt.',
  'feedback.settings.live': 'Ook op de live site',
  'feedback.settings.live.hint': 'Anders alleen op previews.',
  'feedback.settings.public': 'Je verzamelt feedback van het publiek',
  'feedback.settings.public.detail':
    'De live site is openbaar en feedback geven kan zonder in te loggen. Reken op spam: voor open feedback geldt per site een maximum. Is dat bereikt, dan neemt de site niets nieuws meer aan. Je organisatie moet deze verzameling ook in haar eigen privacyverklaring noemen.',
  'feedback.settings.public.detail.previews':
    'Iedereen met de link naar een preview kan feedback geven zonder in te loggen. Reken op spam: voor open feedback geldt per site een maximum. Is dat bereikt, dan neemt de site niets nieuws meer aan. Je organisatie moet deze verzameling ook in haar eigen privacyverklaring noemen.',
  'feedback.settings.saved': 'Feedback-instelling opgeslagen',
  'feedback.settings.saved.off': 'Bezoekers zien de feedbackknop niet meer.',
  'feedback.settings.failed': 'Feedback-instelling niet opgeslagen',
  'feedback.settings.failed.detail': 'Opslaan lukte niet. Probeer het opnieuw.',
  'feedback.settings.summary.off': 'Feedback staat uit. Alleen een beheerder van deze site kan dat veranderen.',
  'feedback.settings.summary.previews': 'Feedback staat aan op de previews.',
  'feedback.settings.summary.live': 'Feedback staat aan op de previews en de live site.',
  'feedback.settings.summary.account': 'Geven kan alleen met een account.',
  'feedback.settings.summary.name': 'Geven kan met een account of een zelf ingevulde naam.',
  'feedback.settings.summary.anonymous': 'Geven kan ook zonder naam.',
  'feedback.settings.show': 'De feedbackknop weer laten zien',
  'feedback.settings.show.hint': 'Heb je de knop op deze site verborgen tot je de browser sluit?',

  // -- No page carries the button -------------------------------------------
  'feedback.nowhere.previews': 'Deze site heeft geen previews, dus nu heeft geen enkele pagina de feedbackknop.',
  'feedback.nowhere.publish':
    'Er staat nog geen versie live en deze site heeft geen previews, dus nog geen enkele pagina heeft de feedbackknop.',
  'feedback.nowhere.previews.admin':
    'Zet feedback ook op de live site aan, of publiceer een preview met de CLI of vanuit je repository.',
  'feedback.nowhere.publish.admin': 'Publiceer eerst een versie van de site.',
} as const;
