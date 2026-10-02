# OpenAmpere

[Quellcode](https://github.com/Gr33ndev/OpenAmpere) · [Fehler melden](https://github.com/Gr33ndev/OpenAmpere/issues) · [Lizenz](LICENSE) · [Drittanbieter-Lizenzen](THIRD_PARTY_LICENSES.md)

**Lokale App für Solaranlagen mit Batteriespeicher, ganz ohne Cloud.**

OpenAmpere spricht direkt im Heimnetz mit dem Wechselrichter, speichert alle Daten lokal und zeigt sie im Browser an. Auf dem Smartphone lässt sich die Seite zum Home-Bildschirm hinzufügen und verhält sich dann wie eine App. Kein Konto, keine Cloud, keine Abhängigkeit von einem Hersteller-Server.

> Unabhängiges Community-Projekt. Hintergrund und rechtliche Hinweise stehen [am Ende dieser Seite](#hintergrund--rechtliche-hinweise).

## Unterstützte Geräte

| Gerät | Status |
|---|---|
| FoxESS H3 / H3 Smart / H3 Pro | ✅ Anzeige und Steuerung. Steuerung ist ab Werk aus. Neue oder alte Registerkarte wird automatisch erkannt. |
| SAJ H2 / HS2 | ✅ Anzeige. Die Steuerung wird erst freigegeben, wenn der Treiber an echten Geräten geprüft ist. |
| Wallbox, Wärmepumpe, Heizstab | geplant |

**Der Gerätetyp wird automatisch erkannt.** Die Einrichtung probiert nacheinander alle bekannten Geräte, und zwar nur lesend: FoxESS auf Geräteadresse 247, SAJ auf 1 und 2. Geräteadresse und Hersteller muss man also nicht kennen. Die Verbindung läuft über Modbus TCP, Standard ist Port 502. Neue Treiber sind willkommen, siehe `src/openampere/drivers/registry.py`.

## Funktionen

- **Dashboard:**
  - Energiefluss live für PV, Haus, Netz und Speicher mit Ladestand
  - Tageswerte
  - Autarkie
  - **Solar nach Modulfeldern:** Leistung, Spannung und Strom je PV-Eingang (MPPT), etwa für Süddach, Westdach oder Garage
  - **Temperaturen:** Wechselrichter, Speicher, Batteriezellen
- **Report:**
  - Leistungskurve über den Tag
  - Energie pro Tag, Woche, Monat und Jahr
  - Ertrag je Modulfeld
  - Temperaturverlauf
  - Autarkie und Eigenverbrauch
- **Speicher & Notstrom:** Notstrom-Reserve, Ladegrenzen, Betriebsmodus
- **Einspeisebegrenzung:** maximale Einspeiseleistung anzeigen und ändern, etwa 60 %, 70 % oder ohne Begrenzung. **Erhöhen oder Aufheben ist nur mit schriftlicher Zustimmung des Netzbetreibers zulässig.** Die App verlangt dafür eine ausdrückliche Bestätigung und protokolliert Datum und Zeichen der Zustimmung.
- **Verlauf aus der bisherigen Cloud übernehmen:** Unter **Mehr → Daten & Sicherung** den API-Schlüssel aus der bisherigen Hersteller-App eintragen und den Import starten.
  - Er läuft im Hintergrund mit höchstens einer Anfrage pro Minute.
  - Nach einem Neustart macht er dort weiter, wo er aufgehört hat.
  - Alternativ lässt sich ein ZIP aus dem [Export-Werkzeug](tools/cloud-export/) einlesen.
- **Cloud-kompatible Schnittstelle:** `/api/v1/customer/installation` und `/api/v1/installation/{id}/now/all/power` antworten wie die bisherige Cloud-Kunden-API. Vorhandene Werkzeuge stellt man nur auf die neue Adresse um.
- **Geplant:**
  - Laden nach Strompreis
  - Ersparnis-Berechnung
  - Wallbox und Wärmepumpe
  - Siehe auch [docs/architektur.md](docs/architektur.md).

## Installation mit Docker

```bash
git clone <repo-url> openampere && cd openampere
docker compose up -d --build
```

Danach `http://<server-ip>:8080` im Browser öffnen. Ein **Einrichtungsassistent** sucht den Wechselrichter im Heimnetz, alternativ gibt man die IP-Adresse ein. Er testet die Verbindung und speichert sie. Alle weiteren Einstellungen erreicht man in der App unter **Mehr**:
- Verbindung
- Speicher & Notstrom
- Stromtarif
- Steuerung
- Darstellung
- Daten & Sicherung

Für eine automatisierte Installation lassen sich alle Werte zusätzlich per `data/config.yaml` (siehe `config.example.yaml`) oder per Umgebungsvariable `OPENAMPERE_…` vorgeben. Umgebungsvariablen haben Vorrang und erscheinen in der App als „fest eingestellt“.

### Betrieb hinter einem Modbus-Proxy

Hängt am Wechselrichter noch ein anderer Energiemanager (z. B. die bisherige Smartbox), teilt man den Modbus-Zugang oft über einen **Modbus-TCP-Proxy**. OpenAmpere kommt damit zurecht:
- In der App unter **Mehr → Verbindung** Adresse, **Port** und **Geräteadresse** des Proxys eintragen. Die Netzsuche nutzt den dort eingestellten Port.
- Meldet der Proxy kurzzeitig, dass der Wechselrichter nicht antwortet (Modbus-Fehler 10/11), oder kommt eine Antwort zu spät, wiederholt OpenAmpere die Anfrage. Erst nach mehreren Fehlschlägen in Folge gilt die Verbindung als getrennt. Dabei werden keine Register fälschlich als ungültig gemerkt.
- Bei langsamen Proxys oder WLAN das **Zeitlimit pro Anfrage** erhöhen (Mehr → Verbindung → Erweitert).
- Ein schreibgeschützter Proxy lehnt Steuerbefehle ab. OpenAmpere zeigt das als verständliche Meldung an.

**Wichtig:** Der Wechselrichter erlaubt nur wenige gleichzeitige Modbus-Verbindungen. Wenn noch ein anderer Energiemanager (z. B. eine bisherige Smartbox) oder eine andere Integration mit ihm spricht, kann es zu Verbindungsabbrüchen kommen.

## Entwicklung ohne echte Anlage

OpenAmpere enthält einen Simulator für den FoxESS H3.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m openampere.simulator --port 5020 --speed 20
```

```bash
OPENAMPERE_INVERTER_HOST=127.0.0.1 OPENAMPERE_INVERTER_PORT=5020 OPENAMPERE_SERVER_PORT=8089 .venv/bin/python -m openampere
```

```bash
.venv/bin/python -m openampere.demo_data --db data/openampere.db --days 60
```

Der letzte Befehl ist optional und erzeugt Demo-Verlaufsdaten.

Web-App mit Live-Reload, sie leitet die API an Port 8089 weiter:

```bash
cd web && npm install && npm run dev
```

Tests:

```bash
.venv/bin/pytest
```

## Sicherheit bei Steuerfunktionen

Alles, was auf den Wechselrichter schreibt, ist ab Werk **aus**. Freigegeben wird es in der App unter **Mehr → Steuerung**, mit Sicherheitsabfrage:
- Nach der Freigabe läuft die Steuerung zunächst im **Probemodus**. Änderungen werden dann nur protokolliert.
- Erst wenn man den Probemodus ausdrücklich beendet, werden Werte an den Wechselrichter gesendet.
- Jede Änderung landet mit altem und neuem Wert im Protokoll und wird nach dem Schreiben vom Gerät zurückgelesen.

Fernsteuerung nutzt immer den eingebauten Watchdog des Wechselrichters. Stürzt OpenAmpere ab, fällt die Anlage selbstständig in ihren normalen Betrieb zurück.

## Hintergrund & rechtliche Hinweise

**Wie OpenAmpere entstanden ist:**
- Die Energiekonzepte Deutschland GmbH (EKD) hat Insolvenz angemeldet ([Bericht im Handelsblatt](https://www.handelsblatt.com/unternehmen/energie/solarenergie-solarspezialist-energiekonzepte-deutschland-meldet-insolvenz-an/100258988.html)).
- Ihre App „Ampere.IQ“ funktioniert nur über Server von EKD. Mit deren Abschaltung verlieren Anlagenbesitzer den Zugriff auf ihre Daten und Einstellungen.
- OpenAmpere entstand als lokale Alternative von Betroffenen für Betroffene.

**Warum es mit EKD-Anlagen funktioniert:** OpenAmpere spricht direkt mit den verbauten Wechselrichtern. Die von EKD als „Ampere.StoragePro E3“ vertriebenen Speicher basieren auf der FoxESS-H3-Serie, die älteren „Ampere.StoragePro“ auf SAJ H2/HS2. OpenAmpere funktioniert genauso mit diesen Geräten aus anderen Quellen.

**Keine Verbindung zu EKD oder den Herstellern:** OpenAmpere ist ein unabhängiges, inoffizielles Community-Projekt. Es steht in keinerlei Verbindung zur Energiekonzepte Deutschland GmbH, zu deren Insolvenzverwaltung, zu FoxESS, SAJ oder Kiwigrid. Es wurde von ihnen weder beauftragt noch autorisiert oder unterstützt.

**Marken:** „AMPERE“, „Ampere.IQ“, „Ampere.StoragePro“ sowie alle weiteren genannten Produkt- und Firmennamen sind Marken oder Bezeichnungen ihrer jeweiligen Inhaber. Alle Rechte daran liegen selbstverständlich bei diesen. Sie werden hier ausschließlich genannt, um zu beschreiben, mit welchen Geräten OpenAmpere zusammenarbeitet. Der Projektname bezieht sich auf die physikalische Einheit Ampere.

**Kein fremder Code:** OpenAmpere enthält keinen Code, keine Grafiken und keine Texte der Ampere.IQ-App. Der Cloud-Import nutzt ausschließlich die öffentlich dokumentierte Kunden-API mit dem persönlichen Schlüssel des jeweiligen Nutzers.

**Nutzung auf eigene Verantwortung:** Steuerfunktionen sind ab Werk ausgeschaltet. Wer Einstellungen am Wechselrichter ändert, insbesondere die Einspeisebegrenzung, ist selbst für die Einhaltung der Netzanschlussbedingungen verantwortlich. Die Hinweise in der App sind keine Rechtsberatung.

## Lizenz

[MIT](LICENSE). Hinweise zu verwendeten Quellen und Marken stehen in [NOTICE](NOTICE), die Lizenzen aller mitgelieferten Open-Source-Komponenten in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Diese Datei wird mit `scripts/third_party_licenses.py` erzeugt. Bitte neu erzeugen, wenn sich Abhängigkeiten ändern.
