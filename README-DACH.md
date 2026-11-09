# Harvey für Deutschland, Österreich und die Schweiz

Deutsche Fassung von [Harvey](https://github.com/ethanplusai/harvey) (Basis 6b6a23a, MIT-Lizenz). Keine Rechtsberatung.

## Schnellstart

Als Plugin in Claude Code (ab Version 2.1.275):
```
/plugin install harvey-dach --marketplace Marl0n98/harvey-dach
```
Ältere Versionen: erst `/plugin marketplace add Marl0n98/harvey-dach`, dann `/plugin install harvey-dach@harvey-dach`.

Dann sag: **"Richte Harvey für mich ein"** (oder `/harvey-dach:harvey`). Claude klont das Repo nach `~/harvey-dach`, fragt nach deinem Business und öffnet das Dashboard. Die Lead-Prüfung startest du mit `/harvey-dach:leads-pruefen`.

Ohne Plugin:
```bash
git clone https://github.com/Marl0n98/harvey-dach.git
cd harvey-dach
claude
```

Dann sag: **"Richte Harvey für mich ein"**. Danach findest du Firmen mit `harvey discover`, baust die Lead-Liste mit `harvey leads` und legst deinen Mail-Stil mit `harvey stil` fest.

## Was anders ist
- Alles auf Deutsch: Befehle, Hilfetexte, Dashboard (Tastenkürzel F Freigeben, X Ablehnen, B Bearbeiten) und Hinweise.
- Saubere Zuordnung: Daten einer Website landen nur bei der Firma, der sie gehört. Leitet eine Website auf eine fremde Domain weiter, wird nichts übernommen. Eine Adresse, die zu mehreren Personen passen könnte (Hans und Heike Müller), bekommt keine Person. Fremde Namen im Impressum (Webdesign, Kammer) zählen nicht als Inhaber. Branchenportale (jameda, Doctolib, Gelbe Seiten, Herold, local.ch) gelten nicht als Website einer Firma.
- `brain.language: de`: Mails und Antworten auf Deutsch, per Sie, ß wird für die Schweiz zu ss.
- Suche: deutsche Branchenbegriffe wie Steuerberater, Rechtsanwalt, Dachdecker, Marketingagentur.
- Namen: Umlaute und Impressum ("Geschäftsführer:", "Inhaber:", "Vertreten durch:").
- Abmeldung: deutsche Formulierungen wie "abmelden", "kein Interesse", "aus dem Verteiler".
- Filter: Kammern, Gerichte und Behörden fliegen raus.
- Einwilligungssperre: Kalte Mails nach DE, AT und CH bleiben in der Outbox stehen, bis Adresse oder Domain in `compliance.consent_list` steht. Das gilt auch für freigegebene Mails und auf Autopilot.

- Signale: Stellenanzeigen mit "(m/w/d)", deutsche Karriere-Seiten und Buchungstools. Beobachtete Signale gehen jetzt auch in den Mail-Entwurf.

## Beispielkonfiguration für harvey.local.yaml
```yaml
brain:
  language: de
compliance:
  opt_out_line: 'Kein Interesse? Antworten Sie einfach "abmelden", dann schreibe ich nicht mehr.'
  consent_required_countries: [DE, AT, CH]
  consent_list: []          # nur Adressen oder Domains mit nachweisbarer Einwilligung
usage:
  quiet_hours: {start: "20:00", end: "08:00", timezone: "Europe/Berlin"}
icp:
  geography: ["Berlin, Deutschland", "Wien, Österreich", "Zürich, Schweiz"]
```

## Noch offen
- Die Einwilligungssperre hält auch .com- und Gmail-Adressen zurück, sobald `icp.geography` einen Ort in DE, AT oder CH nennt oder leer ist und `brain.language: de` gilt. Bei Instantly fliegen solche Kontakte aus dem Upload.
- Orte als "Berlin, Deutschland" schreiben, "Berlin, DE" gilt weiter als Delaware.
- Impressumspflichtangaben unter Mails sind nicht eingebaut, nur `postal_address` und `opt_out_line`.
- Der Precleared-Check kennt nur GB und US als Absenderland.
- Echter Versand und echte Postfächer sind nicht getestet, der Gesamtablauf lief mit einem Fake-Postfach.
- Signale gibt es nur auf einem Teil der Websites, Werbe-Pixel sind hinter Cookie-Bannern meist unsichtbar.
- Ohne Schlüssel für einen Prüfdienst (Reoon, ZeroBounce, Hunter) fragt Harvey selbst beim Mailserver nach (`HARVEY_SMTP_CHECK`, standardmäßig an, auch in `harvey run` und `harvey contacts`). Das läuft über deine eigene IP. Nur „bestätigt“ macht eine Adresse versandfähig, Catch-all und Microsoft bleiben geraten. Abschalten mit `HARVEY_SMTP_CHECK=0`.

## Möglichst viele Firmen finden

`geography` darf ganze Länder enthalten: `Deutschland` (eine Abfrage pro Bundesland), `Österreich`, `Schweiz`. Mit OpenStreetMap holt `harvey discover` dann alle eingetragenen Firmen. `harvey contacts --limit 5000` sucht die Kontakte ohne das Limit pro Durchlauf von `harvey run`. Beides schreibt und verschickt nichts.

## Lead-Liste als Excel und CSV

```bash
.venv/bin/harvey discover --no-profile
.venv/bin/harvey leads
```

`harvey leads` liest jede gefundene Firma: Website, Beschreibung, Telefon aus Karte und Website, E-Mails aus Karte, Website und Impressum, die im Impressum oder Team genannten Personen und wahrscheinliche Adressen für sie. Dann fragt es den Mailserver jeder Domain, ob die Adressen existieren (SMTP bis RCPT, nichts wird gesendet). Ergebnis: `data/exports/leads-<Datum>.xlsx` (Blätter „Firmen“ und „E-Mails“) und `.csv` mit Semikolon.

Spalte „Quelle“: Karte, Website, Muster von Website (Schreibweise einer anderen Adresse dort), geraten. Spalte „Prüfung“: bestätigt, ungültig, Catch-all (Server nimmt alles an), angenommen (Microsoft, unsicher), nicht prüfbar.

Von einem privaten Internetanschluss aus steht in der Spalte „Prüfung“ meist „nicht prüfbar“: Viele Mailserver lehnen solche Prüfungen ab, und manche Anbieter sperren Port 25 ganz. Merkt Harvey das, schaltet es die Prüfung für den Lauf ab, statt bei jeder Firma zu warten. Firmen, bei denen das Lesen fehlschlägt, versucht der nächste Lauf noch einmal. Auf einem Server mit fester IP und Reverse-DNS klappen mehr. Alternativ prüft ein selbst gehosteter Reacher (github.com/reacherhq/check-if-email-exists, AGPL-3.0): `REACHER_URL=http://localhost:8080` setzen, dann fragt `harvey leads` ihn statt selbst. Ein Lauf lässt sich abbrechen und fortsetzen; `--refresh` liest alles neu, `--export-only` schreibt nur die Datei.

## Erstkontakt als Frage

```yaml
channels:
  email:
    first_touch: question
```

Statt drei Mails schreibt Harvey eine einzige: eine Beobachtung und eine Frage, ohne Angebot, Link, Terminwunsch oder Folgemails. Erst auf eine Antwort stellt der Entwurf in zwei Sätzen vor, was du machst, und fragt, ob die Person mehr hören will. Fragt jemand, wer du bist, sagt Harvey es offen. Jeder Entwurf wartet auf deine Freigabe.

Rechtlich ändert das nichts: In Deutschland und Österreich zählt der Zweck einer Mail, nicht ihr Wortlaut. Eine Mail, die zu einem Kunden führen soll, ist Werbung, auch ohne Angebot. Die Einwilligungssperre (`compliance.consent_required_countries`) hält solche Mails deshalb weiter zurück. Sie abzuschalten ist deine Entscheidung und dein Risiko (Abmahnung, Unterlassungserklärung).

## Lead-Liste prüfen lassen

Bevor du mit der Liste arbeitest, prüf sie. Ziel: keine E-Mail, keine Ansprechperson und keine Telefonnummer bei der falschen Firma.

```bash
harvey pruefen          # Regeln, kostenlos, jede Zeile
harvey pruefen --live   # zusätzlich jede Website neu lesen
```

Die Regeln finden Adressen fremder Domains, dieselbe Adresse, Nummer oder Person bei mehreren Firmen und Personen, die nicht zur Adresse passen. Sie laufen so lange, bis sich nichts mehr ändert, damit auch die Adresse geprüft wird, die nach einer Entfernung nachrückt. `--live` liest Startseite, Impressum und Kontaktseite jeder Firma neu: Jede Adresse, Nummer und Person von der Website muss dort noch stehen. Leitet die Seite inzwischen auf eine andere Firma weiter, fliegt alles raus, was von ihr stammt (Adressen von der Website, Personen, Telefon, Beschreibung). Was aus der Karte kommt, bleibt. Ergebnis: `data/exports/leads-<Datum>-geprueft.xlsx` mit den Spalten „Zuordnung geprüft“ und „Prüfhinweis“.

Was danach noch unsicher ist, prüfen in Claude Code zwei unabhängige Agenten mit `/leads-pruefen`: `zuordnungs-pruefer` prüft jeden Wert an der Website der Firma, `gegen-pruefer` sucht unabhängig davon Gründe, warum er nicht passt. Ein Wert bleibt nur, wenn beide zustimmen. Entfernen sie die beste Adresse, gehen auch die anderen ungeprüften Adressen von der Website raus, statt ungeprüft nachzurücken. KI-Aufrufe gibt es nur für die unsicheren Zeilen, nicht für die ganze Liste.

Entfernungen addieren sich: Ein neuer Lauf holt nichts zurück, was eine frühere Prüfung oder die Agenten entfernt haben. Entfernte Werte fehlen in der Liste, in Probemails und Entwürfen, und `harvey contacts` legt solche Personen nicht wieder an. Gelöscht wird nichts, der gespeicherte Lead behält alles.

## Entwürfe ansehen, ohne etwas zu senden

```bash
harvey contacts --limit 10
harvey entwuerfe --limit 2
```

`harvey entwuerfe` schreibt erste Mails für höchstens so viele Kontakte und hört dann auf. Die Entwürfe landen in der Outbox (`harvey outbox`, `harvey dashboard`) und warten immer auf deine Freigabe. Im Frage-Modus prüft Harvey jeden Entwurf noch einmal: Link, Produktname, Preis oder eine Terminbitte, und er wird verworfen.

## Eigener Mail-Stil

Wie die Mails klingen, legst du in einer Datei fest, nicht im Code. Am besten
funktionieren zwei bis drei Mails, die du selbst so abschicken würdest.

```bash
harvey stil              # legt skills/email_rules.md aus der Vorlage an
harvey stil --probe 3    # schreibt drei Probemails, speichert und schickt nichts
```

Die Vorlage `skills/email_rules.beispiel.md` hat vier Abschnitte: So schreibe
ich, Erste Mail, Antwort, Nie. Danach in `harvey.local.yaml`:

```yaml
writer:
  email_rules: "skills/email_rules.md"
```

Der Stil gilt für die erste Mail, auch im Modus `first_touch: question`, und
für die Antworten. Harvey übernimmt aus den Beispielen Ton, Länge, Anrede und
Schluss, die Fakten kommen immer von der Website der Firma. Im Frage-Modus
bleibt die erste Mail trotzdem ohne Angebot, egal was in der Datei steht.
`skills/email_rules.md` ist gitignored, deine Mails landen also nicht im Repo.
