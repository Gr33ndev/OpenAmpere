<!-- PR-Titel im Format von Conventional Commits, z. B. „fix(report): keep daily totals after midnight“ (siehe CONTRIBUTING.md). -->

## Worum geht es?

<!-- Kurz: Was ändert dieser PR und warum? -->

Fixes #<!-- Issue-Nummer. Ohne abgestimmtes Issue bitte zuerst eines öffnen (außer bei Tippfehlern). -->

## Art der Änderung

- [ ] Fehlerbehebung
- [ ] Neue Funktion
- [ ] Neues Gerät / neue Register
- [ ] Oberfläche / Texte
- [ ] Dokumentation
- [ ] Build, CI, Abhängigkeiten

## Screenshots

<!-- Pflicht bei Änderungen an der Oberfläche: vorher/nachher, hell und dunkel, in Handybreite (ca. 390 px). -->

| Vorher | Nachher |
|---|---|
|  |  |

## Wie getestet?

<!-- Simulator, echte Anlage (welches Gerät/Firmware), Browser/Handy … -->

## Checkliste

- [ ] `.venv/bin/pytest` ist grün, neue Logik hat Tests.
- [ ] `npm run build` in `web/` läuft ohne Fehler.
- [ ] Texte in der App sind deutsch und für Laien verständlich.
- [ ] Neue Einstellungen sind in der App änderbar, nicht nur per Datei.
- [ ] Neue Funktionen laufen auch in der Demo: neue API-Endpunkte haben eine Antwort in `web/src/demo/server.ts` (`npm run check:demo`).
- [ ] Keine persönlichen Daten (IP-Adressen, Seriennummern, Schlüssel) in Code, Tests oder Screenshots.
- [ ] Kein Code, keine Grafiken oder Texte aus fremden Apps; Quellen für Register/Code sind angegeben.
- [ ] Bei neuen Abhängigkeiten: Lockfiles neu erzeugt (die Lizenzliste aktualisiert ein Workflow automatisch).

### Nur wenn etwas am Wechselrichter geschrieben wird

- [ ] Nur über die Steuerung erreichbar, Testmodus wird respektiert.
- [ ] Grenzwerte werden vor dem Schreiben geprüft, danach wird zurückgelesen und protokolliert.
- [ ] Register sind belegt (Dokumentation, Quelle oder Diagnosebericht eines echten Geräts).
- [ ] Rechtliche Fragen (Einspeisebegrenzung, § 14a EnWG, EEG) sind im Issue geklärt.
