English: [devices.md](devices.md)

# Unterstützte Geräte

Mit welchen Geräten OpenAmpere funktioniert, was es ausliest und steuert und was schon an einer echten Anlage
bestätigt ist. Hier steht nur, was der Programmcode, [registers.md](registers.md) (englisch) und die Issues belegen.

**So liest du diese Seite:**

- **Bestätigt** heißt: Jemand hat es an seiner eigenen Anlage geprüft und im Issue festgehalten. Die Nummer des
  Issues steht dabei.
- **Noch nicht bestätigt** heißt: Die Unterstützung beruht auf Unterlagen aus der Community oder der
  Schnittstellenbeschreibung des Herstellers und ist mit Tests und dem Simulator geprüft, aber noch niemand hat sie
  von einer echten Anlage gemeldet. Gut möglich, dass alles klappt. Sag uns gern Bescheid, siehe
  [unten](#dein-gerät-fehlt-oder-verhält-sich-anders).
- Die Steuerung ist bei jedem Wechselrichter **ab Werk aus** („Nur ansehen“). Unter **Mehr → Steuerung und Protokoll**
  stellst du sie auf „Testen“ oder „Aktiv“.

## Wechselrichter und Speicher

Alle Wechselrichter werden über Modbus TCP verbunden, Standard ist Port 502. Die Einrichtung erkennt das Gerät
automatisch und liest dabei nur. Reihenfolge: FoxESS auf Geräteadresse 247, danach SAJ auf 1 und 2.

| Gerät / Modell | Von EKD verkauft als | Registerkarte / Erkennung (Geräteadresse) | Anzeige | Steuerung | An echter Anlage geprüft | Hinweise |
|---|---|---|---|---|---|---|
| FoxESS H3, neuere Firmware | „Ampere.StoragePro E3“ (siehe Hinweise) | `foxess_h3_new`; gewählt, wenn die Energiezähler ab 39601 antworten (Adresse 247) | Ja | Ja: Betriebsart, Notstrom-Reserve und Grenzen, Fernsteuerung für Laden aus dem Netz, Einspeisebegrenzung | Anzeige: bestätigt (#11, #16). Steuerung: bestätigt (#25, #196) | Welche Registerkarte der E3 nutzt, ist in [registers.md](registers.md) noch offen. #11 hat die neue Registerkarte an einem H3 bestätigt; im Formular steht H3, H3 Smart, H3 Pro und E3 zur Auswahl, das genaue Modell ist also nicht genannt. Die Einspeisebegrenzung (46616) stammt aus Community-Unterlagen zum H3 Smart; an einer echten Anlage mit der neuen Registerkarte ist sie bestätigt (#196). |
| FoxESS H3, ältere Firmware | in den Quellen nicht genannt | `foxess_h3_legacy`; gewählt, wenn die neue Registerkarte nicht antwortet, die Zähler ab 32000 aber schon (Adresse 247) | Ja | Ja: Betriebsart, Notstrom-Reserve und Grenzen, Fernsteuerung für Laden aus dem Netz. Keine Einspeisebegrenzung (kein bekanntes Register) | noch nicht bestätigt | Die Speicher-Gesundheit (SoH, 31090) gibt es erst ab Firmware 1.80. Der Hausverbrauch ist die Summe der drei Phasen (kein Gesamtregister). |
| FoxESS H3 Smart (Modellname `H3-…-Smart` oder `H3-…-M`) | in den Quellen nicht genannt | `foxess_h3_new` (Adresse 247) | Ja | Ja, wie oben | noch nicht bestätigt | Die Register der Einspeisebegrenzung stammen aus Community-Unterlagen zu diesem Modell. |
| FoxESS H3 Pro (Modellname `H3-Pro-…` oder `P3-Pro-…`) | in den Quellen nicht genannt | `foxess_h3_new` (Adresse 247) | Ja | Ja, wie oben | noch nicht bestätigt | |
| SAJ H2 (Typcode 0x005A, 0x005B), SAJ HS2 (0x005C, 0x005D) | „Ampere.StoragePro“ | `saj_h2`; Kennungsblock 0x8F00 und eine Plausibilitätsprüfung von Betriebsart und Ladestand (Adresse 1 oder 2, beide werden probiert) | Ja | Nein, bewusst gesperrt | noch nicht bestätigt | Die Register für die Steuerung sind aus Community-Unterlagen bekannt, aber nicht geprüft. Deshalb bleibt die Steuerung aus, bis der Treiber an echten Geräten geprüft ist. Noch zu prüfen: die Vorzeichen von Speicher- und Netzleistung und ob der PV-Zähler alle MPP-Tracker erfasst. Manchen Firmware-Ständen fehlen die summierten Netzzähler (0x4167); dann nimmt OpenAmpere die Zähler von L1. |
| SAJ AS2 (Typcode 0x0055, 0x0056) und andere Codes aus dem SAJ-Bereich 0x0050–0x006F | in den Quellen nicht genannt | `saj_h2`, wie oben. Unbekannte Codes aus diesem Bereich werden mit einer Warnung im Protokoll angenommen | Ja | Nein | noch nicht bestätigt | Wird mit derselben Registerkarte wie H2 und HS2 gelesen. |

Mehr zur FoxESS-H3-Familie:

- **Der Modellname ist nur ein Hinweis.** OpenAmpere prüft immer, welche Registerkarte wirklich antwortet, und nimmt
  bevorzugt die neuere. Ob die Messwerte als Input-Register (FC04) oder Holding-Register (FC03) gelesen werden, erkennt
  OpenAmpere ebenfalls selbst, weil die Quellen sich da widersprechen.
- Liegt die Erkennung falsch, kannst du Registerkarte und Leseverfahren unter **Mehr → Verbindung → Für Experten**
  selbst wählen.
- **Die bisherige Smartbox kann Einstellungen zurücksetzen,** weil sie ihre Sollwerte aus der Cloud holt. In #25 hat
  ein Nutzer der Smartbox den Internetzugang gesperrt; danach blieben seine Änderungen an der Notstrom-Reserve und das
  preisbasierte Laden aus dem Netz bestehen. Mehr dazu im Abschnitt „Die bisherige Smartbox setzt Einstellungen
  zurück“ der [README](../README.de.md).
- **Die Untergrenze im Notstrombetrieb darf 0 % sein** (#69). Ob jedes Gerät und jede Firmware 0 % annimmt, ist noch
  nicht bestätigt.
- **Stromausfall-Erkennung:** OpenAmpere nutzt Bit 0 von Register 39065. In #89 war das Bit in einer einzelnen
  Messung gesetzt, während die Anlage ins Netz einspeiste. Seitdem braucht OpenAmpere zwei Messungen hintereinander
  und ignoriert das Bit, solange der Netzzähler Bezug oder Einspeisung misst. In #93 wurde ein absichtlich
  ausgeschalteter Wechselrichter als Stromausfall gezählt.
- **Temperaturen:** Register 39141 (Wechselrichter) zählt in 0,1 °C, abgeglichen mit einer zweiten Anzeige (#11).
  Die „Speicher“-Temperatur aus 37611 ist die Temperatur der BMS-Elektronik, nicht der Zellen (#16).

Einzelheiten zu den Registern und Quellen: [registers.md](registers.md) (englisch) und [NOTICE](../NOTICE).

## Heizstäbe

| Gerät / Modell | Verbindung | Anzeige | Steuerung | An echter Anlage geprüft | Hinweise |
|---|---|---|---|---|---|
| my-PV AC ELWA-E, AC ELWA 2, AC THOR | Modbus TCP, Port 502, Geräteadresse 1 | Leistungsvorgabe (1000), Wassertemperatur (1001), Zieltemperatur (1002), Status (1003) | Nur die Leistungsvorgabe (Register 1000), stufenlos nach Solarüberschuss, wahlweise auch mit günstigem Netzstrom | noch nicht bestätigt | In der Weboberfläche des Heizstabs die Ansteuerung auf „Modbus TCP“ stellen und die Zeitüberschreitung etwas länger als den Takt von OpenAmpere wählen; ohne neuen Wert schaltet der Heizstab ab. OpenAmpere schreibt kein anderes Register, weil der Hersteller darum bittet, andere nicht häufig zu schreiben. Mit einem Simulator getestet. |

## Relais und geschaltete Geräte

Für Heizstäbe ohne Modbus, Wärmepumpen und andere Verbraucher. Diese Geräte werden nur ein- oder ausgeschaltet,
mit Mindestlaufzeit und Mindestpause. Ihre Leistung liest OpenAmpere nicht aus, es rechnet mit der Leistung, die du
einträgst.

| Gerät | So schaltet OpenAmpere | An echter Anlage geprüft | Hinweise |
|---|---|---|---|
| Shelly-Relais der 1. Generation | `http://<Adresse>/relay/<Kanal>?turn=on` bzw. `off` | noch nicht bestätigt | Kanal wählbar. |
| Shelly ab der 2. Generation | `http://<Adresse>/rpc/Switch.Set?id=<Kanal>&on=true` bzw. `false` | noch nicht bestätigt | Kanal wählbar. |
| Beliebiges Gerät mit Web-Adressen | Eine Adresse zum Einschalten, eine zum Ausschalten (http oder https) | noch nicht bestätigt | Jedes Gerät im Heimnetz, das sich über den Aufruf einer Adresse schalten lässt. |
| Wärmepumpe mit SG-Ready-Eingang | Über eines der Relais oben, das den SG-Ready-Kontakt schaltet | noch nicht bestätigt | OpenAmpere schaltet nur das Relais; was die Wärmepumpe mit dem Signal macht, stellst du an der Wärmepumpe ein. |

## Wallboxen

| Gerät | Verbindung | Anzeige | Steuerung | An echter Anlage geprüft | Hinweise |
|---|---|---|---|---|---|
| Wallboxen und Fahrzeuge, die [evcc](https://evcc.io) unterstützt | REST-Schnittstelle von evcc; evcc bekommt Netz-, Solar- und Speicherwerte von OpenAmpere (`/api/evcc/site`) | Ladepunkte, Ladeleistung, Ladevorgänge; Ladestand und Reichweite, wenn das Auto in evcc eingerichtet ist | Lademodus, Ladeziel, Mindestladung, Ladeplan, Vorrang Hausspeicher | noch nicht bestätigt | OpenAmpere steuert Wallboxen nicht selbst; welche Wallboxen gehen, entscheidet evcc, siehe [evcc-Dokumentation](https://docs.evcc.io). Einrichtung: [evcc.de.md](evcc.de.md). |

## Dein Gerät fehlt oder verhält sich anders?

1. Öffne in der App **Mehr → Diagnose → Diagnose starten**. Die Diagnose liest nur und ändert nichts.
2. Tippe auf **Bericht kopieren** und füge den Bericht in das
   [Formular für Geräteberichte](https://github.com/Gr33ndev/OpenAmpere/issues/new?template=device_report.yml) ein.
   Bitte IP-Adressen, Passwörter und API-Schlüssel entfernen. Du kannst gern auf Deutsch schreiben.

Jeder Bericht hilft, eine Zeile von „noch nicht bestätigt“ auf „bestätigt“ zu bringen.
