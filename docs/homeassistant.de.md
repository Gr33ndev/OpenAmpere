# Home Assistant

English: [homeassistant.md](homeassistant.md)

Mit der Integration **OpenAmpere** für Home Assistant siehst du Leistung, Energie, Ladestand und Zustand deiner
Anlage in Home Assistant, auch im Energie-Dashboard. Wenn du es erlaubst, kannst du den Speicher von dort aus
steuern, etwa in Automationen.

> **Wichtig: Erst OpenAmpere installieren.** Die Integration ist nur die Verbindung zu einem laufenden OpenAmpere,
> sie ersetzt es nicht. OpenAmpere läuft auf einem Rechner im Heimnetz (z. B. einem Raspberry Pi) und spricht als
> einziges Programm mit dem Wechselrichter. Home Assistant spricht nur mit OpenAmpere.
> → [Installation von OpenAmpere](../README.de.md#installation)

```
Home Assistant ──(HTTPS, Zugang)──▶ OpenAmpere ──(Modbus)──▶ Wechselrichter
```

Warum nicht direkt per Modbus? Der FoxESS erlaubt nur wenige Modbus-Verbindungen gleichzeitig. Zwei Programme am
Wechselrichter behindern sich gegenseitig und überschreiben sich die Einstellungen. Über OpenAmpere gibt es nur
eine Verbindung, und jeder Befehl aus Home Assistant durchläuft dieselben Prüfungen wie in der App.

## Installation

1. **OpenAmpere installieren und einrichten**, siehe [README](../README.de.md#installation). Der Wechselrichter muss in
   OpenAmpere verbunden sein.
2. **Integration über HACS installieren.** Mit dem Button öffnet sich das Repository direkt in deinem Home Assistant:

   [![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gr33ndev&repository=OpenAmpere&category=integration)

   Von Hand: HACS → ⋮ → Benutzerdefinierte Repositories → `https://github.com/Gr33ndev/OpenAmpere`, Typ
   „Integration“ → OpenAmpere herunterladen → Home Assistant neu starten.
3. **Integration hinzufügen:** Einstellungen → Geräte & Dienste → Integration hinzufügen → OpenAmpere.

   [![Integration hinzufügen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=openampere)

4. **Verbinden**, auf einem von zwei Wegen:

   **Koppeln (empfohlen):**
   - Adresse von OpenAmpere eingeben, z. B. `192.168.178.20`, ohne `http://` und ohne `:8080`. HTTPS-Port ist
     `8443`, das Installations-Script nennt ihn am Ende.
   - Home Assistant zeigt einen 6-stelligen Code. In OpenAmpere unter **Mehr → Verbundene Apps** erscheint die
     Anfrage mit einem Code.
   - Nur wenn beide Codes **gleich** sind: Berechtigung wählen und „Code stimmt – erlauben“ tippen. Fertig.

   **Verbindungscode:**
   - In OpenAmpere unter **Mehr → Verbundene Apps** einen Zugang erstellen und den Verbindungscode kopieren.
   - In Home Assistant „Verbindungscode einfügen“ wählen und den Code einfügen. Die Adresse ist im Code enthalten.
     Nur wenn Home Assistant OpenAmpere unter einer anderen Adresse erreicht als dein Browser, trägst du sie
     zusätzlich ein.

## Was du in Home Assistant bekommst

**Live per Push**, so schnell wie OpenAmpere den Wechselrichter abfragt (Standard alle 10 Sekunden):

| Entität | Hinweis |
|---|---|
| PV-Leistung, Hausverbrauch, Netzleistung, Speicherleistung | Netz: + Bezug, − Einspeisung. Speicher: + Entladen, − Laden |
| Leistung je Modulfeld | Namen wie in OpenAmpere, ausgeblendete Felder fehlen |
| PV-Erzeugung, Verbrauch, Netzbezug, Einspeisung, Speicher geladen/entladen | Gesamtzähler in kWh, **passend fürs Energie-Dashboard** |
| Ladestand, Speicher-Gesundheit, -Spannung, -Strom, -Temperatur, Wechselrichter-Temperatur | |
| Wechselrichter verbunden, Inselbetrieb (Stromausfall), Lädt aus dem Netz, Alarm | Ja/Nein |
| Je Heizstab/Schalter: Leistung, Eingeschaltet, Temperatur | |

**Jede Minute:** Strompreis jetzt, Speicher-Einstellungen, Steuerung (Nur ansehen / Testen / Aktiv).

**Energie-Dashboard:** Einstellungen → Dashboards → Energie. Netzbezug und Einspeisung bei „Stromnetz“,
PV-Erzeugung bei „Solarmodule“, Speicher geladen/entladen bei „Batteriespeicher“.

## Steuern

Nur mit der Berechtigung **„Lesen + Steuern“** und nur, solange die Steuerung in OpenAmpere eingeschaltet ist
(Mehr → Steuerung und Protokoll). Ist sie aus, sind diese Entitäten in Home Assistant nicht verfügbar.

| Entität | |
|---|---|
| Betriebsmodus | Eigenverbrauch, Einspeisung bevorzugen, Notstromreserve, Spitzenlast begrenzen |
| Notstrom-Reserve, Ladegrenze, Untergrenze im Notstrombetrieb | in % |
| Laden aus dem Netz, Ladeziel aus dem Netz | einmal in OpenAmpere einrichten (rechtliche Hinweise) |
| Betriebsart je Heizstab/Schalter | Automatik (Überschuss), Aus, Boost |

Jede Änderung prüft OpenAmpere wie in der App: Grenzwerte, Testmodus, Rücklesen. Im Protokoll steht sie mit
dem Namen des Zugangs. **Höchstens 6 Änderungen pro Stunde je Speicher-Einstellung**, damit eine Automation in einer
Schleife den Speicher des Wechselrichters nicht abnutzt. Darüber lehnt OpenAmpere mit einer Meldung ab.

**Bewusst nicht aus Home Assistant änderbar:**
- der Hauptschalter der Steuerung und der Testmodus
- die Einspeisebegrenzung
- direkte Lade-/Entladeleistung
- Einstellungen, Tarife, Passwort, Zugänge, Datensicherung, Updates und die Geräte-Konfiguration

Die Wallbox steuerst du in Home Assistant über die
[evcc-Integration](https://www.home-assistant.io/integrations/evcc/).

## Sicherheit

- **Eigener Zugang je App:** Er ist nur für diese Schnittstelle gültig, nicht für die Web-App, und lässt sich unter
  Mehr → Verbundene Apps jederzeit entfernen („zuletzt benutzt“ steht dabei). OpenAmpere speichert nur einen Hash.
- **Verschlüsselt mit festem Zertifikat:** OpenAmpere erzeugt beim ersten Start ein eigenes Zertifikat. Home
  Assistant merkt sich dessen Fingerabdruck und lehnt jedes andere ab. Wer sich im Netz dazwischenschaltet, fällt
  auf, beim Koppeln an unterschiedlichen Codes.
- **Nur freigegebene Befehle**, siehe oben. Der Hauptschalter bleibt in OpenAmpere.
- **Keine persönlichen Daten in Home Assistant:** keine Seriennummern, Zählernummern oder Netzbetreiber-Daten. Sie
  würden sonst in Backups von Home Assistant landen. Die Diagnose der Integration schwärzt Zugang und Adresse.
- Der Zugang selbst steht, wie bei allen Integrationen, unverschlüsselt in den Daten von Home Assistant und damit in
  dessen Backups. Gelangt ein Backup in falsche Hände: Zugang in OpenAmpere entfernen und neu koppeln.

## Einstellungen und Fehler

- **Weniger Live-Werte:** Integration → Konfigurieren → „Mindestabstand zwischen Live-Werten“, z. B. 30 Sekunden,
  schont kleine Systeme mit SD-Karte. 0 übernimmt jeden Wert.
- **„OpenAmpere ist nicht erreichbar“:** Adresse und HTTPS-Port prüfen. Läuft OpenAmpere in Docker mit
  `network_mode: host` (Standard), ist der Port direkt offen. Unter Mehr → Verbundene Apps steht, ob HTTPS läuft.
- **Port 8443 belegt** (z. B. durch einen UniFi-Controller): in der `docker-compose.yml` unter `environment`
  `OPENAMPERE_SERVER_TLS_PORT: "8444"` eintragen, `docker compose up -d` ausführen und mit Port 8444 koppeln.
- **„Neu verbinden“ in Home Assistant:** Der Zugang wurde in OpenAmpere entfernt oder das Zertifikat hat sich
  geändert, z. B. nach einer Neuinstallation ohne den Ordner `data`. Einfach neu koppeln.
- **HTTPS abschalten:** `OPENAMPERE_SERVER_TLS_PORT: "0"`. Dann können sich keine Apps verbinden.

Die Integration hat dieselbe Version wie OpenAmpere und kommt aus denselben Releases. Halte beide aktuell: Passen
sie nicht zusammen, meldet Home Assistant das beim Start.
