# Mein Mail-Stil

Diese Datei bestimmt, wie Harvey deine Mails schreibt. Kopier sie nach
`skills/email_rules.md` (oder `harvey stil`), ersetz die Beispiele durch Mails,
die du selbst so abschicken würdest, und trag in `harvey.local.yaml` ein:

    writer:
      email_rules: "skills/email_rules.md"

Mit `harvey stil --probe 3` siehst du drei Probemails, bevor irgendetwas
rausgeht. Harvey übernimmt aus deinen Beispielen Ton, Länge, Anrede und
Schluss, nie die Fakten: Was über die Firma drinsteht, kommt immer aus deren
Website. Am stärksten wirken zwei bis drei echte Beispielmails, mehr als jede
Regel.

Lisa und die Beispiel GmbH sind erfunden. Name und Firma unter deinen Mails
nimmt Harvey aus `persona` in `harvey.local.yaml`, nicht aus dieser Datei.

## So schreibe ich

- Anrede: "Hallo Frau Huber," oder "Hallo Herr Huber,", mit Titel "Hallo Herr Dr. Huber,".
  Ohne Namen: "Hallo zusammen,".
- Sie, nicht du.
- Höchstens vier kurze Sätze. Die Frage kommt spätestens im zweiten Satz.
- Kein Einstieg über die Branche ("In vielen Praxen ..."), kein Lob, kein
  "Ich bin auf Ihre Seite gestoßen".
- Schluss: nur Vorname und Firma, kein "Beste Grüße aus ...".
- Betreff: zwei bis vier Wörter, klein geschrieben, über die Firma, nicht über mich.

## Erste Mail

Beispiel 1

    Betreff: termine bei huber physio

    Hallo Frau Huber,

    auf Ihrer Seite kann man Termine nur telefonisch ausmachen. Ist das
    bewusst so, oder kommt das Telefon da manchmal nicht hinterher?

    Lisa
    Beispiel GmbH

Beispiel 2

    Betreff: neue mitarbeiter

    Hallo Herr Maier,

    Sie suchen gerade zwei Elektriker. Finden Sie die Leute eher über
    Empfehlungen oder über Anzeigen?

    Lisa
    Beispiel GmbH

## Antwort, wenn jemand antwortet

Erst auf die Antwort eingehen, dann in einem Satz sagen, was ich mache, dann
fragen, ob die Person mehr wissen will. Kein Preis, kein Link, kein Termin.

Beispiel

    Hallo Frau Huber,

    danke, das kenne ich von vielen Praxen. Wir bauen genau dafür eine
    Online-Terminbuchung, die sich in Ihren Kalender einträgt.
    Soll ich Ihnen kurz zeigen, wie das bei Ihnen aussehen würde?

    Lisa
    Beispiel GmbH

## Nie

- "Ich hoffe, diese Mail erreicht Sie gut"
- "Ich bin auf ... gestoßen", "Mir ist aufgefallen"
- "spannend", "beeindruckend", "innovativ", "maßgeschneidert"
- "in der heutigen Zeit", "gerade in Zeiten von"
- Gedankenstriche, Ausrufezeichen, Emojis
