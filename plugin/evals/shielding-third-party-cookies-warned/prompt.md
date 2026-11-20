---
description: A non-public site with separate files gets the third-party cookie block explained before it is published.
tags: [shielding]
max_turns: 8
allowed_tools: [Skill, Read, Glob, Grep]
---

Zet ons rapport uit ./dist op de site team-aurora/rapport op
https://beheer.toon.example.nl. Alleen genodigden mogen erbij: collega's van
het ministerie, die werken allemaal via Citrix met Edge. Dit staat in dist:

    dist/index.html
    dist/assets/style.css
    dist/assets/grafieken.js
    dist/img/logo.png

En dit is de index.html:

    <!doctype html>
    <html lang="nl">
      <head>
        <meta charset="UTF-8">
        <title>Rapport</title>
        <link rel="stylesheet" href="assets/style.css">
        <script src="assets/grafieken.js" defer></script>
      </head>
      <body>
        <img src="img/logo.png" alt="Logo">
        <main id="rapport"></main>
      </body>
    </html>

Schrijf het plan en het publiceercommando op voordat je iets uitvoert.
