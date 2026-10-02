# Mitmachen bei OpenAmpere

Danke, dass du helfen möchtest! OpenAmpere lebt davon, dass Betroffene ihre Geräte, Fehler und Ideen einbringen –
auch ohne Programmierkenntnisse.

## Kurz gesagt

1. **Erst ein Issue, dann der Code.** Für alles außer Tippfehlern bitte zuerst ein Issue öffnen oder ein vorhandenes
   nutzen. So klären wir vorher, ob und wie etwas umgesetzt wird, und niemand arbeitet umsonst.
2. **Kleine Pull Requests.** Ein PR löst ein Problem. Lieber zwei kleine als einen großen.
3. **UI-Änderung = Screenshots.** Vorher/Nachher, hell und dunkel, in Handybreite.
4. **Tests und Build müssen grün sein.**
5. **Keine persönlichen Daten** (IP-Adressen, Seriennummern, API-Schlüssel, Passwörter) in Issues, Code, Screenshots
   oder Logs.

## Ohne Code helfen

- **Fehler melden:** Vorlage „Fehler melden“ wählen. Die Version steht unter Mehr → Über.
- **Gerät melden:** Unter Mehr → Diagnose einen Bericht erstellen und mit der Vorlage „Gerätebericht“ teilen. Das ist
  die wichtigste Hilfe, um weitere Wechselrichter und Firmware-Stände zu unterstützen. Die Diagnose liest nur und
  ändert nichts am Gerät.
- **Ideen:** Vorlage „Funktionswunsch“. Beschreibe vor allem das Problem, nicht nur die Lösung.
- **Texte und Übersetzungen:** Unklare Formulierungen in der App sind ein Fehler – bitte melden.

**Sicherheitslücken** bitte nie als öffentliches Issue, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

## Ablauf für Code-Beiträge

1. Issue öffnen oder kommentieren, dass du es übernehmen möchtest. Warte bei größeren Änderungen auf eine kurze
   Rückmeldung zum Lösungsweg.
2. Repository forken, einen Branch anlegen (`fix/…`, `feature/…`, `docs/…`).
3. Änderung umsetzen, Tests ergänzen, lokal prüfen (siehe unten).
4. Pull Request mit der Vorlage öffnen und das Issue verlinken (`Fixes #123`).
5. Review abwarten und Anmerkungen einarbeiten. Bitte keine Force-Pushes, während ein Review läuft.

## Entwicklungsumgebung

Voraussetzungen: Python 3.11+ und Node.js 22. Eine echte Anlage brauchst du nicht – der Simulator bildet FoxESS- und
SAJ-Geräte nach, inklusive Proxy-Fehlern und Verzögerungen. Die Befehle stehen im
[README](README.md#entwicklung-ohne-echte-anlage).

Vor dem PR:

```bash
.venv/bin/pytest
```

```bash
cd web && npm run build
```

Ändern sich Abhängigkeiten, Lockfiles und Lizenzliste neu erzeugen (siehe README, Abschnitt Entwicklung):

```bash
.venv/bin/python scripts/third_party_licenses.py
```

## Regeln für den Code

- **Wie der Code drumherum:** Benennung, Kommentardichte und Stil an die Umgebung anpassen. Kommentare und Bezeichner
  auf Englisch, alle Texte in der App auf Deutsch.
- **Verständlich für Laien:** Die App richtet sich an Anlagenbesitzer ohne Technikwissen. Keine Fachbegriffe ohne
  Erklärung, Fehlermeldungen als ganze deutsche Sätze mit einem Hinweis, was man tun kann.
- **Alles in der App einstellbar:** Neue Optionen gehören in die Oberfläche, nicht nur in eine Konfigurationsdatei.
- **Tests:** Neue Logik bekommt Tests, Fehlerbehebungen einen Test, der den Fehler vorher zeigt. Tests mit dem
  Simulator stoppen den Collector immer in einem `finally`-Block.
- **Barrierefreiheit:** Tippflächen mindestens 44 px, ausreichender Kontrast in hell und dunkel, sinnvolle
  `aria`-Attribute.
- **Keine neuen Abhängigkeiten** ohne Absprache im Issue.

### Wechselrichter und Steuerung

Fehler beim Schreiben können Geräte, Garantie oder die Netzanschlussbedingungen betreffen. Deshalb gilt zusätzlich:

- **Neue Register nur mit Quelle:** Herstellerdokumentation, ein Projekt mit kompatibler Lizenz (Quelle in
  `docs/registers.md` und `NOTICE` nennen) oder ein Diagnosebericht von einem echten Gerät.
- **Schreibende Funktionen** sind nur über die Steuerung erreichbar, respektieren den Testmodus, prüfen Grenzwerte vor
  dem Schreiben, lesen danach zurück und schreiben ins Protokoll.
- **Dauerhaftes Steuern** (z. B. Laden nach Preis) nur über die Fernsteuerung mit Watchdog, nie über dauerhaft
  gespeicherte Register.
- **Rechtliche Themen** (Einspeisebegrenzung, § 14a EnWG, EEG) im Issue ansprechen, bevor Code entsteht.

### Fremder Code und Marken

- **Kein Code, keine Grafiken und keine Texte aus fremden Apps**, insbesondere nicht aus der Ampere.IQ-App oder deren
  dekompiliertem Code. Übernommen werden nur Fakten (Registeradressen, Datenformate, Schnittstellen).
- Code aus anderen Open-Source-Projekten nur mit kompatibler Lizenz und Quellenangabe.
- Firmen- und Produktnamen nur beschreibend verwenden, siehe [rechtliche Hinweise](README.md#hintergrund--rechtliche-hinweise).

## Commits

- Kurze Betreffzeile im Imperativ auf Englisch, z. B. `Fix daily totals after midnight`.
- Im Text das Warum erklären, nicht nur das Was.
- Keine Links zu privaten Chats, Tickets oder Sitzungen und keine persönlichen Daten in Commits.

## Lizenz

Mit deinem Beitrag stimmst du zu, dass er unter der [MIT-Lizenz](LICENSE) des Projekts veröffentlicht wird.

## Umgang miteinander

Freundlich, sachlich, geduldig – viele hier sind keine Entwickler, sondern Betroffene, die ihre Anlage weiter nutzen
wollen. Herabsetzende Kommentare werden entfernt.
