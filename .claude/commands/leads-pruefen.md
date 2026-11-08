---
description: Lead-Liste prüfen, bis kein Wert mehr bei der falschen Firma oder Person steht (Regeln, Websites neu lesen, zwei unabhängige Prüf-Agenten)
---

Prüfe die Lead-Liste von Harvey so, dass am Ende keine E-Mail, keine Ansprechperson und keine Telefonnummer bei der falschen Firma steht. Es wird nichts gesendet.

1. **Regeln und Websites:** Führe `.venv/bin/harvey pruefen --live` im Harvey-Ordner aus (direkt, ohne `source`; ist Harvey global installiert, reicht `harvey`) (bei sehr großen Listen zuerst `harvey pruefen` ohne `--live`, das kostet nichts). Lies danach die Datei mit den offenen Fällen, die der Befehl nennt (`data/exports/pruefung-<Datum>.json`). Sind es 0 Fälle, bist du fertig: nenne die geprüfte Excel-Datei.
2. **Zwei unabhängige Prüfer:** Teile die Fälle in Pakete von höchstens 15. Starte für jedes Paket die Subagenten `zuordnungs-pruefer` und `gegen-pruefer` getrennt und gleichzeitig, beide mit denselben Fällen. Gib dem Gegenprüfer nie das Urteil des ersten Prüfers.
3. **Zusammenführen:** Jeder Fall hat bei E-Mail und Telefon eine Quelle (`email_quelle`, `telefon_karte` / `telefon_website`).
   - **Werte von der Website** (Quelle Website, Muster, geraten, `telefon_website`): Beide sagen „gehoert“, dann **behalten**. Sagt einer „gehoert_nicht“ oder „unklar“, dann **entfernen**. Lieber ein leeres Feld als ein falsches.
   - **Werte aus der Karte** (Quelle Karte, `telefon_karte`): Sie stammen aus demselben Karteneintrag wie die Firma selbst. **Entfernen** nur, wenn mindestens ein Prüfer „gehoert_nicht“ mit Beleg sagt. „unklar“ (etwa weil die Website nicht lädt) heißt **behalten**.
   - **Ansprechperson:** Es zählt nur, ob die Person zu dieser Firma gehört. Wie die Rolle heißt (Inhaberin, Medieninhaberin, Diensteanbieterin, Geschäftsführer, Physiotherapeutin), ist egal. Entfernen nur bei „gehoert_nicht“ oder wenn beide „unklar“ sagen.
   Schreib die Entscheidungen als JSON nach `data/exports/entscheidungen-<Datum>.json`:
   ```json
   [{"company_id": "...", "email": "behalten|entfernen", "ansprechpartner": "behalten|entfernen", "telefon": {"<nummer>": "behalten|entfernen"}, "begruendung": "ein Satz mit Beleg"}]
   ```
4. **Übernehmen:** Führe `.venv/bin/harvey pruefen --anwenden data/exports/entscheidungen-<Datum>.json` aus. Wurde dabei eine beste Adresse entfernt, rückt die nächste nach und gilt als neuer Fall: Führe danach `.venv/bin/harvey pruefen` (ohne `--live`) noch einmal aus und prüf neue Fälle wie in Schritt 2 und 3, höchstens zwei Runden. Am Ende steht die geprüfte Liste in `data/exports/leads-<Datum>-geprueft.xlsx`.
5. **Bericht:** Sag kurz, wie viele Firmen geprüft wurden, wie viele Werte entfernt wurden und warum (die häufigsten Gründe), und nenne die Datei. Zeig keine Namen oder Adressen im Chat, die nicht nötig sind.

Regeln: Ändere keine Dateien außer den beiden JSON-Dateien. Schick keine Mails, gib nichts frei, ändere nichts an der Konfiguration.
