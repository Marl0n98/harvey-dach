---
description: Lead-Liste prüfen, bis kein Wert mehr bei der falschen Firma oder Person steht (Regeln, Websites neu lesen, zwei unabhängige Prüf-Agenten)
---

Prüfe die Lead-Liste von Harvey so, dass am Ende keine E-Mail, keine Ansprechperson und keine Telefonnummer bei der falschen Firma steht. Es wird nichts gesendet.

1. **Regeln und Websites:** Führe `.venv/bin/harvey pruefen --live` im Harvey-Ordner aus (direkt, ohne `source`; ist Harvey global installiert, reicht `harvey`) (bei sehr großen Listen zuerst `harvey pruefen` ohne `--live`, das kostet nichts). Lies danach die Datei mit den offenen Fällen, die der Befehl nennt (`data/exports/pruefung-<Datum>.json`). Sind es 0 Fälle, bist du fertig: nenne die geprüfte Excel-Datei.
2. **Zwei unabhängige Prüfer:** Teile die Fälle in Pakete von höchstens 15. Starte für jedes Paket die Subagenten `zuordnungs-pruefer` und `gegen-pruefer` getrennt und gleichzeitig, beide mit denselben Fällen. Gib dem Gegenprüfer nie das Urteil des ersten Prüfers.
3. **Zusammenführen:** Für jeden Wert gilt:
   - Beide sagen „gehoert“: **behalten**.
   - Einer sagt „gehoert_nicht“ oder „unklar“: **entfernen**. Lieber ein leeres Feld als ein falsches.
   Schreib die Entscheidungen als JSON nach `data/exports/entscheidungen-<Datum>.json`:
   ```json
   [{"company_id": "...", "email": "behalten|entfernen", "ansprechpartner": "behalten|entfernen", "telefon": {"<nummer>": "behalten|entfernen"}, "begruendung": "ein Satz mit Beleg"}]
   ```
4. **Übernehmen:** Führe `.venv/bin/harvey pruefen --anwenden data/exports/entscheidungen-<Datum>.json` aus. Das schreibt die geprüfte Liste neu (`data/exports/leads-<Datum>-geprueft.xlsx`).
5. **Bericht:** Sag kurz, wie viele Firmen geprüft wurden, wie viele Werte entfernt wurden und warum (die häufigsten Gründe), und nenne die Datei. Zeig keine Namen oder Adressen im Chat, die nicht nötig sind.

Regeln: Ändere keine Dateien außer den beiden JSON-Dateien. Schick keine Mails, gib nichts frei, ändere nichts an der Konfiguration.
