/**
 * Dutch catalogue section: the notifications about new feedback. The e-mail
 * choice a member makes per level (profile, group, site) and the Mattermost
 * channel of a group or site.
 */
export const notificationsNl = {
  // -- E-mail ---------------------------------------------------------------
  'notifications.email.heading': 'E-mail over nieuwe feedback',
  'notifications.email.intro':
    'De e-mail bevat de site, de pagina, de soort, het tijdstip en of er een schermafbeelding bij zit, met een link. De tekst van de feedback en de afzender staan er nooit in. De e-mail gaat naar het adres van je login.',
  'notifications.email.list.label': 'E-mail over nieuwe feedback',
  'notifications.email.profile.label': 'Stuur me e-mail over nieuwe feedback',
  'notifications.email.profile.hint':
    'Dit geldt voor elke groep en site waar je niets anders hebt gekozen.',
  'notifications.email.state.on': 'aan',
  'notifications.email.state.off': 'uit',
  'notifications.email.group.follow': 'Volg mijn profiel (nu: {state})',
  'notifications.email.group.follow.hint': 'Je eigen keuze voor een site in deze groep gaat voor.',
  'notifications.email.group.on': 'Aan',
  'notifications.email.group.on.hint':
    'Je krijgt e-mail over nieuwe feedback op elke site in deze groep waar je voor de site niets anders hebt gekozen.',
  'notifications.email.group.off': 'Uit',
  'notifications.email.group.off.hint':
    'Je krijgt geen e-mail over nieuwe feedback van deze groep, tenzij je voor een site iets anders kiest.',
  'notifications.email.site.follow': 'Volg de groep en mijn profiel',
  'notifications.email.site.follow.group': 'Overgenomen van de groep: {state}',
  'notifications.email.site.follow.profile': 'Van je profiel: {state}',
  'notifications.email.site.follow.default': 'Nergens ingesteld: {state}',
  'notifications.email.site.on': 'Aan',
  'notifications.email.site.on.hint': 'Je krijgt e-mail over nieuwe feedback op deze site.',
  'notifications.email.site.off': 'Uit',
  'notifications.email.site.off.hint': 'Je krijgt geen e-mail over nieuwe feedback op deze site.',
  'notifications.email.saved': 'E-mailkeuze opgeslagen',
  'notifications.email.saved.on': 'Je krijgt nu e-mail over nieuwe feedback.',
  'notifications.email.saved.off': 'Je krijgt nu geen e-mail over nieuwe feedback.',
  'notifications.email.failed': 'Kon de e-mailkeuze niet opslaan',
  'notifications.email.failed.detail': 'Probeer het opnieuw.',
  'notifications.email.loadFailed': 'Kon de e-mailkeuze niet laden',
  'notifications.email.loadFailed.detail': 'Laad de pagina opnieuw.',

  // -- Mattermost -----------------------------------------------------------
  'notifications.webhook.heading': 'Mattermost-kanaal',
  'notifications.webhook.intro':
    'Laat elke nieuwe feedback in een Mattermost-kanaal verschijnen, met een inkomende webhook. Een site zonder eigen kanaal gebruikt dat van de groep.',
  'notifications.webhook.status.own':
    'Berichten gaan naar een Mattermost-kanaal op {host}. Taal van de berichten: {language}.',
  'notifications.webhook.status.group':
    'Deze site heeft geen eigen kanaal. Berichten gaan naar het kanaal van de groep op {host}.',
  'notifications.webhook.status.none': 'Geen Mattermost-kanaal.',
  'notifications.webhook.warning': 'Iedereen in het kanaal leest de feedback mee',
  'notifications.webhook.warning.detail':
    'Met een webhook komt de tekst van elke nieuwe feedback (afgekapt op 500 tekens, zonder naam of e-mailadres van de afzender) ook in het Mattermost-kanaal. De tekst blijft daar staan als de feedback in Toon wordt verwijderd. Iedereen in dat kanaal leest mee.',
  'notifications.webhook.url.label': 'Webhook-URL',
  'notifications.webhook.url.hint':
    'Plak de URL van een inkomende webhook. Toon laat hem nooit meer zien.',
  'notifications.webhook.url.replace': 'Opslaan vervangt de huidige webhook.',
  'notifications.webhook.language.label': 'Taal van de berichten',
  'notifications.webhook.save': 'Webhook opslaan',
  'notifications.webhook.remove': 'Webhook verwijderen',
  'notifications.webhook.saved': 'Webhook opgeslagen',
  'notifications.webhook.saved.detail': 'Berichten gaan nu naar een Mattermost-kanaal op {host}.',
  'notifications.webhook.failed': 'Kon de webhook niet opslaan',
  'notifications.webhook.failed.detail': 'Probeer het opnieuw.',
  'notifications.webhook.removed': 'Webhook verwijderd',
  'notifications.webhook.removeFailed': 'Kon de webhook niet verwijderen',
  'notifications.webhook.removeFailed.detail': 'Probeer het opnieuw.',
  'notifications.webhook.loadFailed': 'Kon het Mattermost-kanaal niet laden',
  'notifications.webhook.loadFailed.detail': 'Laad de pagina opnieuw.',
  'notifications.webhook.confirm.title': 'De webhook verwijderen?',
  'notifications.webhook.confirm.text':
    'Nieuwe feedback komt niet meer in het kanaal. Wat er al staat, blijft staan.',
  'notifications.webhook.confirm.keep': 'Bewaren',
  'notifications.webhook.confirm.confirm': 'Webhook verwijderen',
} as const;
