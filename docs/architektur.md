# Architektur

OpenAmpere ist eine lokal laufende, quelloffene App für Solaranlagen mit Batteriespeicher. Sie spricht direkt im Heimnetz mit dem Wechselrichter, ganz ohne Cloud.

## Grundsätze

- **Lokal:** Alle Daten bleiben auf dem eigenen Server. Die App funktioniert ohne Internet.
- **Für Endnutzer:** Alles lässt sich in der Web-App einstellen. Ein Einrichtungsassistent erkennt das Gerät automatisch, Konfigurationsdateien braucht man nicht.
- **Sicher beim Schreiben:**
  - Steuerfunktionen sind ab Werk aus und haben zusätzlich einen Testmodus.
  - Werte werden vor dem Schreiben geprüft und danach zurückgelesen.
  - Jede Änderung landet im Protokoll.
- **Robust:** Die App verträgt Modbus-Proxys, Verbindungsabbrüche und Neustarts. Sie läuft im Hintergrund weiter, auch wenn kein Browser offen ist.

## Komponenten

```
┌──────────── Docker-Container (Proxmox-LXC/VM, Raspberry Pi, NAS) ────────────┐
│                                                                              │
│  drivers/              collector           storage (SQLite)       api        │
│  ├ registry (auto)  ─▶ Abfrage alle    ─▶  Rohwerte (Tage)     ─▶ REST       │
│  ├ foxess/ (H3)        5–60 s, eine        15-min-Energie         WebSocket  │
│  ├ saj/ (H2/HS2)       TCP-Verbindung      Energie je Modulfeld   Cloud-kompat.│
│  └ modbus.py (Basis)                       Einstellungen, Protokoll  Web-App │
│                                                                    (PWA)     │
│  control.py: Speicher, Notstrom, Einspeisebegrenzung (Schalter, Testmodus)  │
│  cloud_import.py: Verlauf aus der bisherigen Cloud (1 Anfrage/min, fortsetzbar)│
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ Modbus TCP (direkt oder über einen Modbus-Proxy)          ▲ Browser/Smartphone
     Wechselrichter / Speicher
```

| Baustein | Aufgabe |
|---|---|
| `drivers/modbus.py` | Basis für alle Treiber: eine dauerhafte, serialisierte Verbindung. Vorübergehende Fehler wie Zeitüberschreitung oder Proxy-Aussetzer werden wiederholt. Ablehnungen durch das Gerät (illegale Adresse) führen zum Einzellesen; Register werden nur gesperrt, wenn das Gerät sie wirklich ablehnt. |
| `drivers/registry.py` | Liste der Treiber und automatische Erkennung, nur lesend: FoxESS auf Geräteadresse 247, dann SAJ auf 1 und 2. |
| `drivers/foxess/` | FoxESS H3 / H3 Smart / H3 Pro. Neue oder alte Registerkarte und Funktionscode werden automatisch erkannt. |
| `drivers/saj/` | SAJ H2 / HS2. Vorerst nur Anzeige. |
| `collector.py` | Fragt das Gerät ab, verteilt Live-Werte per WebSocket und speichert. Einzelne Aussetzer trennen die Verbindung nicht. |
| `storage.py` | SQLite-Datenbank. Jeder Zugriff, auch jedes Lesen, läuft über eine Sperre. Eine Dateisperre verhindert, dass zwei Instanzen dieselbe Datenbank benutzen. |
| `runtime.py`, `config.py` | Einstellungen: Standardwerte < `config.yaml` < Einstellungen aus der Web-App < Umgebungsvariablen. Umgebungsvariablen erscheinen in der App als „fest eingestellt“. Änderungen greifen ohne Neustart. |
| `control.py` | Speicher-Einstellungen und Einspeisebegrenzung. Die Begrenzung folgt der angegebenen Regel (60 % / 70 % der Modulleistung, Wert vom Netzbetreiber, keine). Mehr als die Regel erlaubt, wird abgelehnt; bei einem Wert vom Netzbetreiber braucht jede Erhöhung dessen Zustimmung, deren Aktenzeichen protokolliert wird. |
| `auth.py` | Zugriffsschutz: ein lokales Passwort (scrypt), Sitzungs-Cookie, CSRF-Header, Prüfung von Origin und Host (gegen DNS-Rebinding), Sperre nach Fehlversuchen. Lesen im Heimnetz ist frei, Ändern braucht eine Anmeldung. |
| `tariffs.py` | Stromtarife mit Startdatum (Festpreis oder dynamisch), Börsenpreise von aWATTar je Viertelstunde, Ersparnis-Berechnung. |
| `charging.py` | Laden aus dem Netz nach Preis oder Zeitfenster, nur über die Fernsteuerung mit Watchdog (3 min); prüft, dass der Speicher wirklich lädt. |
| `consumers.py` | Überschuss verteilen nach Reihenfolge: my-PV-Heizstab stufenlos (`drivers/mypv.py`), Shelly/HTTP an oder aus mit Hysterese und Mindestzeiten, Speicher-Vorrang, optional günstiger Netzstrom, Rücksicht auf ein wartendes Auto. Läuft im schnellen Takt (5 bis 15 s). |
| `evcc.py` | Anbindung an evcc für Wallboxen: liest `/api/state` fehlertolerant, sendet Lademodus, Ladeziel und Ladeplan. Unter `/api/evcc/site` bekommt evcc Netz, Solar und Speicher, siehe [evcc.md](evcc.md). |
| `diagnostics.py` | Diagnose nur lesend: Blöcke mit FC03/FC04, Skalierung, Exportlimit, Fernsteuerung, optional Verbindungsgrenze; dazu passiv gesammelte Verbindungsabbrüche, Zählerauffälligkeiten und Rücksetzzeiten der Tageszähler. |
| `notify.py` | Benachrichtigungen über ntfy, jedes Ereignis nur einmal. |
| `cloud_import.py` | Übernimmt den Verlauf aus der bisherigen Hersteller-Cloud über deren Kunden-API, alternativ per ZIP aus `tools/cloud-export`. Eigene Messwerte werden nie überschrieben. |
| `simulator.py` | Modbus-TCP-Simulator für FoxESS und SAJ, mit Modulfeldern, Temperaturen, Einspeisebegrenzung und Proxy-Fehlern. Damit lässt sich ohne echte Anlage entwickeln und testen. |
| `web/` | React-PWA: Dashboard, Report, „Mehr“ mit allen Einstellungen, Einrichtungsassistent. |

## Datenmodell

- **samples:** Rohwerte jeder Abfrage (Leistungen, Ladestand, Leistung je PV-Eingang, Temperaturen). Wie lange sie aufbewahrt werden, ist einstellbar.
- **energy_15m:** Energie je Viertelstunde aus den Zählern des Wechselrichters. Wird dauerhaft aufbewahrt. `source` gibt die Herkunft an, `local` oder `cloud`.
- **pv_input_15m:** Energie je PV-Eingang (Modulfeld) und Viertelstunde, aus der Leistung aufsummiert.
- **meta / control_log:** Einstellungen, Importstand, Protokoll aller Steuerbefehle.

## Repo-Inhalt

- `src/openampere/`: Backend, Treiber, Simulator
- `web/`: Web-App (PWA)
- `tools/cloud-export/`: sichert den Verlauf aus der bisherigen Cloud, solange diese läuft
- `docs/`: Architektur und Registerdokumentation
- `scripts/third_party_licenses.py`: erzeugt die Lizenzhinweise der mitgelieferten Komponenten

Das Repo enthält bewusst keinen Code, keine Grafiken und keine Texte fremder Apps. Übernommen wurden nur Fakten wie Schnittstellen, Registeradressen und Datenmodelle.
