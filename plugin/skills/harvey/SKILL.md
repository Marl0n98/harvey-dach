---
name: harvey
description: Richtet Harvey DACH ein (deutscher KI-Vertriebler für Claude Code) und öffnet das Dashboard. Nutze diesen Skill, wenn jemand Harvey installieren, einrichten oder starten will, Kunden oder Firmen finden möchte, eine Lead-Liste braucht oder sagt "Richte Harvey für mich ein".
---

# Harvey DACH einrichten

Harvey findet passende Firmen über OpenStreetMap, liest ihre Websites, baut eine Lead-Liste und schreibt Mail-Entwürfe auf Deutsch. Gesendet wird nichts ohne Freigabe, und kalte Mails nach DE, AT und CH hält die Einwilligungssperre zurück.

Sprich Deutsch, per du, frag immer nur eine Sache auf einmal.

## 1. Harvey-Ordner finden oder anlegen

- Liegt im aktuellen Ordner eine `README-DACH.md`, ist das der Harvey-Ordner.
- Sonst: `~/harvey-dach`. Gibt es den nicht, klonen:
  ```bash
  git clone https://github.com/Marl0n98/harvey-dach.git ~/harvey-dach
  ```
Alle weiteren Befehle laufen in diesem Ordner.

## 2. Installieren

Python 3.11 oder neuer:
```bash
cd ~/harvey-dach && python3 -m venv .venv && .venv/bin/pip install -e .
```
Fehlt Python 3.11+, sag das und nenn den Weg (`brew install python@3.12` auf dem Mac).

## 3. Nach dem Business fragen

Frag nacheinander:
1. Wie heißt deine Firma, und was bietest du an (ein Satz)?
2. Wen willst du erreichen (Branche) und wo (Stadt oder Region, Land)?

Kopier zuerst die vollständige Vorlage (`cp harvey.yaml harvey.local.yaml`), denn Harvey liest nur diese eine Datei und braucht alle Abschnitte (`persona`, `product`, `icp`). Setz darin `persona` (Name, Firma) und `product` (Name, Beschreibung, Nutzen) aus den Antworten, ohne erfundene Zahlen, und dazu:
- `brain.language: de`
- `channels.email.first_touch: question` (die erste Mail ist nur eine Frage, das Angebot kommt erst nach einer Antwort)
- `icp.industries` und `icp.geography` aus den Antworten
- `compliance.consent_required_countries: [DE, AT, CH]` und eine leere `consent_list`

Schreib das Angebot in zwei, drei Sätzen nach `skills/product_knowledge.md`. Erfinde keine Zahlen oder Kunden.

## 4. Sicher starten

```bash
.venv/bin/harvey sending pause
.venv/bin/harvey signals --confirm free
```
Verbinde kein Postfach und trag keine Zugangsdaten ein, solange der Nutzer das nicht ausdrücklich will.

## 5. Dashboard öffnen

Starte das Dashboard im Hintergrund und öffne es im Browser, ohne den Link mit dem Token in den Chat zu schreiben:
```bash
nohup .venv/bin/harvey dashboard > data/dashboard.log 2>&1 &
sleep 3 && open "$(.venv/bin/harvey dashboard --url)"
```
Unter Linux statt `open` den Link mit `xdg-open` öffnen. Den Link und `data/dashboard.log` nie in den Chat schreiben, denn darin steht das Token.

Sag danach kurz, was als Nächstes geht:
- **Firmen finden:** im Dashboard unter „Entdecken“ Branche und Ort eingeben und starten (kostenlos über OpenStreetMap).
- **Lead-Liste:** `.venv/bin/harvey leads` liest die Websites und schreibt eine Excel nach `data/exports/`.
- **Liste prüfen:** `/harvey-dach:leads-pruefen` lässt zwei unabhängige Prüf-Agenten jede unsichere Zuordnung kontrollieren.
- **Eigener Stil:** `.venv/bin/harvey stil` legt `skills/email_rules.md` an, mit `--probe 2` siehst du zwei Probemails.
- **Entwürfe:** `.venv/bin/harvey entwuerfe --limit 2` schreibt erste Mails in den Postausgang des Dashboards. Gesendet wird nichts.

## Regeln

- Nie senden, nie freigeben, die Einwilligungssperre nie abschalten, ohne dass der Nutzer es ausdrücklich sagt.
- Keine echten Personendaten in Chat-Zusammenfassungen, wenn sie nicht nötig sind.
- Keine Rechtsberatung: Werbe-Mails an Firmen brauchen in DE und AT vorher eine Einwilligung.
