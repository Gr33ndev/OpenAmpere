# OpenAmpere

**English:** [README.md](README.md)

[Website & Demo](https://gr33ndev.github.io/OpenAmpere/) · [Häufige Fragen](https://gr33ndev.github.io/OpenAmpere/faq.html) · [Quellcode](https://github.com/Gr33ndev/OpenAmpere) · [Fragen](https://github.com/Gr33ndev/OpenAmpere/discussions) · [Roadmap](https://github.com/users/Gr33ndev/projects/1) · [Fehler melden](https://github.com/Gr33ndev/OpenAmpere/issues) · [Lizenz](LICENSE) · [Drittanbieter-Lizenzen](THIRD_PARTY_LICENSES.md) · [Sicherheit](SECURITY.md) · [Mitmachen](CONTRIBUTING.md) · [Verhaltenskodex](CODE_OF_CONDUCT.md)

**Lokale App für Solaranlagen mit Batteriespeicher, ganz ohne Cloud.**

OpenAmpere spricht direkt im Heimnetz mit dem Wechselrichter, speichert alle Daten lokal und zeigt sie im Browser an. Auf dem Smartphone lässt sich die Seite zum Home-Bildschirm hinzufügen und verhält sich dann wie eine App. Kein Konto, keine Cloud, keine Abhängigkeit von einem Hersteller-Server.

<p align="center">
  <img src="docs/screenshots/overview.png" width="190" alt="Übersicht mit Energiefluss von Solar, Speicher, Netz, Wallbox und Heizstab">
  <img src="docs/screenshots/devices.png" width="190" alt="Geräte: Speicher und Wallbox bedienen">
  <img src="docs/screenshots/report.png" width="190" alt="Auswertung mit Tageswerten und Leistungskurve">
  <img src="docs/screenshots/overview-dark.png" width="190" alt="Übersicht im dunklen Design">
</p>
<p align="center"><sub>Aus der <a href="https://gr33ndev.github.io/OpenAmpere/demo/">Demo</a> mit erfundenen Werten. Neu erzeugen mit <code>scripts/screenshots.sh</code>.</sub></p>

> Unabhängiges Community-Projekt. Hintergrund und rechtliche Hinweise stehen [am Ende dieser Seite](#hintergrund--rechtliche-hinweise).

## Unterstützte Geräte

| Gerät | Was OpenAmpere kann | An echter Anlage bestätigt? |
|---|---|---|
| FoxESS H3, neuere Firmware | Anzeige und Steuerung. Steuerung ist ab Werk aus. Neue oder alte Registerkarte wird automatisch erkannt. | ✅ Anzeige bestätigt, Steuerung teilweise bestätigt |
| FoxESS H3, ältere Firmware / H3 Smart / H3 Pro | Anzeige und Steuerung, bei älterer Firmware ohne Einspeisebegrenzung | Noch nicht bestätigt |
| SAJ H2 / HS2 | Anzeige. Die Steuerung wird erst freigegeben, wenn der Treiber an echten Geräten geprüft ist. | Noch nicht bestätigt |
| Heizstab, Wärmepumpe (SG-Ready) | Schalten bei Solarüberschuss über Shelly-Relais oder Web-Adressen. | Noch nicht bestätigt |
| Heizstab my-PV AC ELWA-E, AC ELWA 2, AC THOR | Stufenlos nach Solarüberschuss, optional mit günstigem Netzstrom. | Noch nicht bestätigt |
| Wallbox | Über [evcc](https://evcc.io): Anzeige und Bedienung in OpenAmpere, siehe [docs/evcc.de.md](docs/evcc.de.md). | Noch nicht bestätigt |
| Home Assistant | Eigene Integration über HACS: Daten live und Steuerung, siehe [Home Assistant](#home-assistant). | – |

**Noch nicht bestätigt** heißt: Die Unterstützung beruht auf Unterlagen der Hersteller oder der Community und ist mit Tests und dem Simulator geprüft, aber noch niemand hat sie von einer echten Anlage gemeldet. Gut möglich, dass alles klappt. Hast du so ein Gerät, hilf mit einem **Gerätebericht**: Starte in der App unter **Mehr → Diagnose** die Diagnose (sie liest nur und ändert nichts), tippe auf **Bericht kopieren** und füge den Bericht in das [Formular für Geräteberichte](https://github.com/Gr33ndev/OpenAmpere/issues/new?template=device_report.yml) ein. Entferne vorher IP-Adressen, Passwörter und API-Schlüssel. Jeder Bericht hilft, eine Zeile auf „bestätigt“ zu bringen.

**Der Gerätetyp wird automatisch erkannt.** Die Einrichtung probiert nacheinander alle bekannten Geräte, und zwar nur lesend: FoxESS auf Geräteadresse 247, SAJ auf 1 und 2. Geräteadresse und Hersteller muss man also nicht kennen. Die Verbindung läuft über Modbus TCP, Standard ist Port 502. Neue Treiber sind willkommen, siehe `src/openampere/drivers/registry.py`.

Vollständige Liste: [docs/devices.de.md](docs/devices.de.md), mit dem, was je Modell angezeigt und gesteuert wird und was genau schon an echten Anlagen bestätigt ist.

## Funktionen

- **Menü:** Übersicht, Geräte (Speicher, Wallbox, Heizstab bedienen und die Reihenfolge für Sonnenstrom festlegen), Auswertung und Mehr (Einrichtung und Einstellungen). Ein Schalter „Nur ansehen / Testen / Aktiv“ legt fest, ob OpenAmpere etwas ändern darf.
- **Übersicht:**
  - Energiefluss live für PV, Haus, Netz und Speicher mit Ladestand, dazu Wallbox und Heizstab
  - Tageswerte
  - Autarkie
  - **Solar nach Modulfeldern:** Leistung, Spannung und Strom je PV-Eingang (MPPT), etwa für Süddach, Westdach oder Garage
  - **Temperaturen:** Wechselrichter, Speicher, Batteriezellen
- **Auswertung:**
  - Leistungskurve über den Tag mit Ladestand, Werte beim Antippen
  - Energie pro Tag (15 oder 60 Minuten), Woche, Monat und Jahr
  - Ertrag je Modulfeld
  - Temperaturverlauf
  - Verbrauch je Gerät (Wallbox, Heizstab) und Ladevorgänge
  - Autarkie, Eigenverbrauch, Verbrauch aufgeteilt nach Haushalt und Geräten, geschätzte Ersparnis
- **Stromtarife:** Festpreis oder dynamischer Tarif (Börsenpreis plus Aufschlag, Deutschland und Österreich) mit Grundpreis, mehrere Tarife mit Startdatum; daraus Ersparnis und Stromkosten unterm Strich.
- **Abschläge im Blick:** Monatliche Abschläge für Strombezug und Einspeisung eintragen (Mehr → Abschläge). Die Auswertung zeigt den Stand heute: bisherige Kosten und Vergütung gegen die Abschläge bis heute, der laufende Monat anteilig.
- **Zählerwerte vom Netzbetreiber:** Mit Smart Meter holt OpenAmpere die Tageswerte aus dem Kundenportal des Netzbetreibers (Mehr → Verbindung → Zählerwerte) und rechnet die Abschläge damit. Bisher für Netze BW, weitere Netzbetreiber können als eigenes Modul dazukommen.
- **Export:** Energiewerte als CSV-Datei für Excel und Co.
- **Speicher & Notstrom:** Notstrom-Reserve, Ladegrenzen, Betriebsmodus
- **Einspeisebegrenzung:** maximale Einspeiseleistung anzeigen und ändern. Man gibt die installierte Modulleistung (kWp) und die geltende Regel an: 60 % nach dem Solarspitzengesetz (bis ein intelligentes Messsystem mit Steuerbox eingebaut ist), die frühere 70-%-Regel, ein fester Wert aus der Netzanschlusszusage (z. B. Nulleinspeisung) oder keine Begrenzung. Die Prozente beziehen sich auf die Modulleistung, nicht auf den Wechselrichter; mehr als die Regel erlaubt, lässt die App nicht zu. **Bei einem festen Wert vom Netzbetreiber ist jede Erhöhung nur mit dessen schriftlicher Zustimmung zulässig**; die App verlangt dafür eine Bestätigung und protokolliert Datum und Zeichen. „Keine Begrenzung“ setzt eine ausdrückliche Erklärung voraus, die ebenfalls protokolliert wird.
- **Verlauf aus der EKD-Cloud übernehmen:** Für bisherige Nutzer der App „Ampere.IQ“. Unter **Mehr → Daten & Sicherung** den API-Schlüssel aus der Ampere.IQ-App eintragen und den Import starten. Der Import nutzt nur die öffentliche Kunden-API mit dem eigenen Schlüssel; OpenAmpere hat nichts mit EKD zu tun, siehe [rechtliche Hinweise](#hintergrund--rechtliche-hinweise).
  - Er läuft im Hintergrund mit höchstens einer Anfrage pro Minute.
  - Nach einem Neustart macht er dort weiter, wo er aufgehört hat.
  - Alternativ lässt sich ein ZIP aus dem [Export-Werkzeug](tools/cloud-export/) einlesen.
- **Cloud-kompatible Schnittstelle:** `/api/v1/customer/installation` und `/api/v1/installation/{id}/now/all/power` antworten wie die bisherige Cloud-Kunden-API. Vorhandene Werkzeuge stellt man nur auf die neue Adresse um.
- **Laden aus dem Netz (experimentell):** zum günstigsten Börsenpreis oder in einem festen Zeitfenster, bis zu einem Ladeziel. Läuft nur über die Fernsteuerung des Wechselrichters mit Zeitbegrenzung, im Testmodus nur protokolliert; vorher sind rechtliche Hinweise zu bestätigen (EEG-Speicher, § 14a EnWG).
- **Überschuss nutzen:** Ein my-PV-Heizstab (AC ELWA-E, AC ELWA 2, AC THOR) folgt dem Solarüberschuss stufenlos, mit Mindestüberschuss, Höchstleistung, Speicher-Vorrang und optional günstigem Netzstrom. Wärmepumpe (SG-Ready) oder andere Geräte schaltet OpenAmpere über Shelly-Relais oder Web-Adressen, nach Priorität, mit Mindestlauf- und Mindestpausenzeit.
- **Speicher-Gesundheit** (Mehr → Meine Anlage): Vollzyklen, Wirkungsgrad seit Inbetriebnahme, Temperatur der wärmsten und kühlsten Zelle und ihr Unterschied, mit Warnung bei auffälligen Werten.
- **Firmware-Änderungen:** OpenAmpere merkt sich die Firmware des Wechselrichters und zeigt, wann sie sich geändert hat. Ein Update kann Register ändern, danach am besten einmal die Diagnose ausführen.
- **Benachrichtigungen** über ntfy: Wechselrichter nicht erreichbar, Störung, überschriebene Einstellung, Speicher voll, günstigster Strom morgen, Speicher prüfen, neue Firmware.
- **Diagnose (nur lesen):** prüft Registerkarte, Funktionscodes, optionale Blöcke, Skalierung, Einspeisebegrenzung, Verbindungsabbrüche und Tageszähler des eigenen Geräts und erstellt einen Bericht zum Teilen. **Vor der ersten Änderung am Wechselrichter einmal ausführen.**
- **Wallbox mit evcc:** Wallboxen steuert das eigenständige Open-Source-Projekt [evcc](https://evcc.io). OpenAmpere liefert evcc die Messwerte von Netz, Solar und Speicher, sodass evcc keine eigene Verbindung zum Wechselrichter braucht, und zeigt die Ladepunkte in der App: Lademodus, Ladeziel, Mindestladung, Ladeplan, Uhrzeit bis zum Ziel und Ladevorgänge. Ist das Auto selbst in evcc eingerichtet, kommen Ladestand und Reichweite dazu, und die Auswertung zeigt gefahrene Kilometer, Kilometer mit Sonnenstrom, Verbrauch pro 100 km und Kosten pro 100 km im Vergleich zu Netzstrom. Ob Wallbox oder Heizstab zuerst Überschuss bekommt, ist einstellbar. Einrichtung: [docs/evcc.de.md](docs/evcc.de.md). Danke an die evcc-Community!
- Siehe auch [docs/architecture.md](docs/architecture.md) (englisch).

## Home Assistant

Die Integration **OpenAmpere** bringt Leistung, Energie (passend fürs Energie-Dashboard), Ladestand und Zustand live
nach Home Assistant, auf Wunsch auch die Steuerung von Speicher, Laden aus dem Netz und Heizstab.

> **Erst OpenAmpere installieren** (siehe [Installation](#installation)), dann die Integration. Sie verbindet sich nur
> mit dem laufenden OpenAmpere und ersetzt es nicht. Nur OpenAmpere spricht mit dem Wechselrichter.

[![HACS Custom](https://img.shields.io/badge/HACS-custom-orange.svg?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=ccc)](https://hacs.xyz)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gr33ndev&repository=OpenAmpere&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=openampere)

Danach in Home Assistant die Integration OpenAmpere hinzufügen und **koppeln**: Adresse von OpenAmpere eingeben, den
6-stelligen Code mit dem unter **Mehr → Verbundene Apps** vergleichen und dort erlauben. Die Verbindung ist
verschlüsselt, jede App bekommt einen eigenen, widerrufbaren Zugang. Gesteuert werden kann nur, was in OpenAmpere
freigegeben ist. Anleitung und Details: [docs/homeassistant.de.md](docs/homeassistant.de.md).

## Installation

**Voraussetzungen:** ein Linux-Rechner im selben Netz wie der Wechselrichter (Raspberry Pi mit 64-Bit-System, NAS, Proxmox …) und Modbus TCP am Wechselrichter eingeschaltet. Bei den von EKD verkauften Speichern ist das schon der Fall, bei anderen Geräten kann es der Installationsbetrieb erledigen.

**1. Installieren:** Auf dem Rechner im Terminal ausführen:

```bash
curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash
```

Lieber erst prüfen, dann ausführen? Dann das Script mit seinen Helfern und `SHA256SUMS` aus dem [neuesten Release](https://github.com/Gr33ndev/OpenAmpere/releases/latest) laden, die Prüfsummen vergleichen und es danach starten. Liegen die Helfer daneben, nimmt `install.sh` diese, statt sie nachzuladen:

```bash
mkdir openampere-install && cd openampere-install
for f in install.sh updater.sh tailscale.sh SHA256SUMS; do
  curl -fsSLO "https://github.com/Gr33ndev/OpenAmpere/releases/latest/download/$f"
done
sha256sum -c SHA256SUMS && bash install.sh
```

Das Script installiert bei Bedarf Docker, fragt nach dem Ordner (Standard `/opt/openampere`), ob es evcc für eine Wallbox mit einrichten soll und ob du OpenAmpere auch von unterwegs nutzen willst (Tailscale, siehe [Zugriff von unterwegs](#zugriff-von-unterwegs)). Beim erneuten Ausführen bleiben die Antworten erhalten. Zeitzone und einen freien Port erkennt es selbst. Am Ende zeigt es die Adresse der App. Was es tut, steht in [scripts/install.sh](scripts/install.sh).

**Updates:** Gibt es eine neue Version, zeigt die App oben einen Hinweis, ein Tipp auf **Aktualisieren** genügt. Unter Mehr → Über OpenAmpere lassen sich Updates auch nachts automatisch installieren. Dafür richtet das Install-Script einen kleinen Helfer-Container ein ([scripts/updater.sh](scripts/updater.sh)): Er hat Zugriff auf Docker, reagiert aber nur auf eine Anfrage-Datei, die die App in den Datenordner schreibt. Dann lädt er die neue Version, startet sie und prüft, ob sie läuft. Startet sie nicht, kommt die bisherige Version zurück. Die App selbst bekommt keinen Zugriff auf Docker. Nach neuen Versionen sucht die App alle 6 Stunden bei GitHub, das lässt sich abschalten. Wer schon vorher mit dem Script installiert hat, führt es einmal erneut aus, damit der Helfer dazukommt.

**2. Im Browser einrichten:** Die angezeigte Adresse öffnen, meist `http://<server-ip>:8080`, und ein **Passwort** festlegen. Ein **Einrichtungsassistent** sucht den Wechselrichter im Heimnetz, alternativ gibt man die IP-Adresse ein. Er testet die Verbindung und speichert sie. Ansehen kann man die Werte im Heimnetz ohne Passwort. Einstellungen ändern, Steuerbefehle und Datensicherung brauchen eine Anmeldung.

**3. Optional: Heizstab oder Wärmepumpe.** Dafür braucht es keine weitere Software. In der App unter **Mehr → Verbindung → Heizstab und weitere Geräte** hinzufügen.

**4. Optional: Wallbox.** Die Wallbox steuert [evcc](https://evcc.io), ein eigenes Open-Source-Projekt. Hat man im Script „Wallbox“ bejaht, läuft evcc schon als zweiter Container unter `http://<server-ip>:7070`, und OpenAmpere kennt seine Adresse. Dann:
1. In evcc Wallbox und Fahrzeug einrichten.
2. In OpenAmpere unter **Mehr → Verbindung → Wallbox** die Zähler-Konfiguration kopieren und in evcc einfügen. So bekommt evcc die Messwerte von Netz, Solar und Speicher von OpenAmpere.

Läuft evcc schon woanders, trägt man unter Mehr → Verbindung → Wallbox dessen Adresse ein. Details stehen in [docs/evcc.de.md](docs/evcc.de.md).

**Wo was ist:** Unter **Geräte** bedient man Speicher (Notstrom-Reserve, Ladegrenzen), Wallbox und Heizstab und legt fest, wer zuerst Sonnenstrom bekommt. Unter **Mehr** liegen Meine Anlage (mit Einspeisebegrenzung), Stromtarif, Verbindung, Steuerung und Protokoll, Benachrichtigungen, Zugriffsschutz, Darstellung, Daten & Sicherung und Diagnose.

**Passwort vergessen?** Führe auf dem Server im Terminal diesen Befehl aus und lege danach in der App ein neues Passwort fest:

```bash
cd /opt/openampere && sudo docker compose exec openampere openampere reset-password
```

Liegt OpenAmpere in einem anderen Ordner (bei der Installation von Hand im Ordner des Repositorys), ersetze `/opt/openampere` durch diesen Ordner.

Probleme beim Einrichten? Siehe [Fehlerbehebung](#fehlerbehebung). Wie du Daten sicherst, auf einen neuen Rechner umziehst oder OpenAmpere wieder entfernst, steht unter [Sichern und Wiederherstellen](#sichern-und-wiederherstellen), [Umziehen auf einen neuen Rechner](#umziehen-auf-einen-neuen-rechner) und [Deinstallieren](#deinstallieren).

### Installation von Hand

Wer selbst bauen oder mitentwickeln will, nimmt das Repository statt des Scripts:

```bash
git clone https://github.com/Gr33ndev/OpenAmpere.git openampere && cd openampere
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
```

Die App läuft im Container als Benutzer 1000 und braucht Schreibrechte auf `data/`. Dort liegt neben der Datenbank `secret.key`, der Schlüssel für die gespeicherten Zugangsdaten. Ohne ihn müssen sie in der App neu eingegeben werden. evcc für eine Wallbox steht in der `docker-compose.yml` als auskommentierter Dienst bereit, siehe [docs/evcc.de.md](docs/evcc.de.md).

### Release prüfen

Die Release-Images baut der [Release-Workflow](.github/workflows/release.yml) dieses Repositorys, mit signiertem Herkunftsnachweis (Build Provenance) und SBOM. Mit der [GitHub CLI](https://cli.github.com) lässt sich prüfen, ob ein Image wirklich hier gebaut wurde:

```bash
gh attestation verify oci://ghcr.io/gr33ndev/openampere:<version> --owner Gr33ndev
```

`<version>` steht ohne führendes `v`, z. B. `0.10.0`, oder `latest`. Das geht für Releases nach 0.9.0. Die SBOM (die Liste der Pakete im Image) zeigt `docker buildx imagetools inspect ghcr.io/gr33ndev/openampere:<version> --format '{{ json .SBOM }}'`.

**Der Update-Helfer prüft das automatisch.** Bevor er eine neue Version startet (Knopf in der App oder nachts), prüft er das Image mit [cosign](https://docs.sigstore.dev/cosign/) gegen den Release-Workflow dieses Repositorys, ein Konto braucht es dafür nicht. Schlägt die Prüfung fehl, wird die neue Version nicht gestartet, die bisherige läuft weiter, und die App zeigt einen Hinweis. Installationen von vor 0.10.1 bekommen diesen Helfer, wenn sie das Install-Script einmal erneut ausführen. Nur für den Notfall schaltet `OPENAMPERE_VERIFY_IMAGES=nein` beim Dienst `updater` die Prüfung ab.

`install.sh`, `updater.sh` und `tailscale.sh` hängen an jedem Release, zusammen mit `SHA256SUMS`. Die Website liefert die Scripts des neuesten Releases aus (mit derselben `SHA256SUMS`), eine Änderung auf `main` erreicht neue Installationen also erst mit einem Release.

### Zugriff von unterwegs

OpenAmpere ist fürs Heimnetz gebaut. Von unterwegs erreicht man es am sichersten über ein **VPN**.

**Am einfachsten mit Tailscale:** Die Frage „von unterwegs nutzen?“ im Install-Script mit Ja beantworten, dann in der App unter **Mehr → Zugriff von unterwegs** auf **Einrichten** tippen und bei Tailscale anmelden (kostenloses Konto, z. B. mit Google, Apple oder Microsoft). Auf dem Handy die Tailscale-App mit demselben Konto anmelden und die Adresse öffnen, die OpenAmpere anzeigt.
- Wer schon installiert hat, führt das Script einmal erneut aus: `curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | OPENAMPERE_TAILSCALE=ja bash`. Mit `OPENAMPERE_TAILSCALE=nein` wird es wieder entfernt.
- Tailscale läuft als eigener Container im Userspace-Modus, ohne Zusatzrechte. Wie beim Update-Helfer bekommt die App keinen Zugriff darauf, sie legt nur eine Anfrage ab ([scripts/tailscale.sh](scripts/tailscale.sh)). Das Senden von Protokolldaten an Tailscale ist abgeschaltet, Funnel (öffentlich ins Internet) wird nicht genutzt.
- **HTTPS (optional):** Unter **Mehr → Zugriff von unterwegs** stellt „HTTPS einschalten“ OpenAmpere unter `https://openampere.<tailnet>.ts.net` mit einem Zertifikat von Tailscale bereit (`tailscale serve`). Dafür muss in der Tailscale-Verwaltung unter DNS „HTTPS Certificates“ eingeschaltet sein; sonst zeigt die App den Link zum Freischalten. Installationen von vor 0.13.0 bekommen das, wenn sie das Install-Script einmal erneut ausführen.
- Tailscale ist ein Dienst der Tailscale Inc. (USA). Er vermittelt die Verbindung, die Daten laufen verschlüsselt direkt zwischen den Geräten.

**Ohne Drittanbieter:** das VPN der FRITZ!Box (WireGuard) oder ein eigener WireGuard-Server. Danach öffnet man die App wie zu Hause über die IP-Adresse des Servers.

**Niemals per Portfreigabe direkt ins Internet stellen.** Wer die App so erreicht, kann den Wechselrichter steuern, sobald das Passwort geknackt oder abgefangen ist (kein HTTPS).

Eigene Hostnamen (z. B. ein Reverse-Proxy im Heimnetz) müssen unter `server.allowed_hosts` eingetragen werden; IP-Adressen, `localhost`, `*.local`, `*.fritz.box`, `*.home.arpa` und Tailscale-Namen (`*.ts.net`) funktionieren ohne Eintrag.

Für eine automatisierte Installation lassen sich alle Werte zusätzlich per `data/config.yaml` (siehe `config.example.yaml`) oder per Umgebungsvariable `OPENAMPERE_…` vorgeben. Umgebungsvariablen haben Vorrang und erscheinen in der App als „fest eingestellt“.

### Betrieb hinter einem Modbus-Proxy

Hängt am Wechselrichter noch ein anderer Energiemanager (z. B. die bisherige Smartbox), teilt man den Modbus-Zugang oft über einen **Modbus-TCP-Proxy**. OpenAmpere kommt damit zurecht:
- In der App unter **Mehr → Verbindung** Adresse, **Port** und **Geräteadresse** des Proxys eintragen. Die Netzsuche nutzt den dort eingestellten Port.
- Meldet der Proxy kurzzeitig, dass der Wechselrichter nicht antwortet (Modbus-Fehler 10/11), oder kommt eine Antwort zu spät, wiederholt OpenAmpere die Anfrage. Erst nach mehreren Fehlschlägen in Folge gilt die Verbindung als getrennt. Dabei werden keine Register fälschlich als ungültig gemerkt.
- Bei langsamen Proxys oder WLAN das **Zeitlimit pro Anfrage** erhöhen (Mehr → Verbindung → Erweitert).
- Ein schreibgeschützter Proxy lehnt Steuerbefehle ab. OpenAmpere zeigt das als verständliche Meldung an.

**Wichtig:** Der Wechselrichter erlaubt nur wenige gleichzeitige Modbus-Verbindungen. Wenn noch ein anderer Energiemanager (z. B. eine bisherige Smartbox) oder eine andere Integration mit ihm spricht, kann es zu Verbindungsabbrüchen kommen.

### Die bisherige Smartbox setzt Einstellungen zurück

Eine bisherige Smartbox holt sich etwa alle 100 Sekunden ihre Soll-Einstellungen aus der Cloud ihres Herstellers und schreibt sie in den Wechselrichter. Damit überschreibt sie Änderungen von OpenAmpere, etwa an Notstrom-Reserve, Betriebsmodus oder Laden aus dem Netz. OpenAmpere erkennt das, zeigt einen Hinweis und kann eine Benachrichtigung „Einstellung überschrieben“ senden.

Abhilfe:
- **Internetzugang der Smartbox sperren**, z. B. in der FRITZ!Box unter Internet → Filter → Kindersicherung (Zugangsprofil „gesperrt“). Ein Nutzer hat so erfolgreich auf preisbasiertes Laden umgeschaltet und die Notstrom-Reserve geändert. Die Smartbox liest dann weiter mit, bekommt aber keine Soll-Werte und keine Updates mehr, und die Hersteller-App zeigt keine aktuellen Daten.
- **Smartbox abklemmen**, wenn sie nicht mehr gebraucht wird. Vorher klären, ob sie für etwas anderes nötig ist, etwa die Steuerung durch den Netzbetreiber.

## Sichern und Wiederherstellen

Die Befehle in diesem und den nächsten Abschnitten gelten für eine Installation mit dem Script im Standardordner `/opt/openampere`. Hast du einen anderen Ordner gewählt, ersetze `/opt/openampere` durch deinen Ordner.

**Sicherung in der App:** Unter **Mehr → Daten & Sicherung** lädt **Datensicherung herunterladen** die komplette Datenbank mit allen Messwerten und Einstellungen als eine Datei herunter (`openampere-backup-<Datum>.db`). Dafür musst du angemeldet sein. Passwörter und API-Schlüssel, etwa für evcc, ntfy, das Kundenportal des Netzbetreibers oder den Ampere.IQ-Import, sind nicht enthalten. Bewahre die Datei an einem anderen Ort auf, zum Beispiel auf deinem Computer oder einem USB-Stick.

**Alles sichern:** Alle Daten von OpenAmpere liegen im Ordner `data` im Installationsordner. Dazu gehört neben der Datenbank die Datei `secret.key`, der Schlüssel für die gespeicherten Passwörter und API-Schlüssel. Wer diesen Ordner sichert, hat alles. Auf dem Server im Terminal:

```bash
cd /opt/openampere
sudo docker compose stop openampere
sudo tar czf ~/openampere-sicherung.tar.gz data
sudo docker compose start openampere
```

Die Datei `openampere-sicherung.tar.gz` liegt danach in deinem Benutzerordner auf dem Server. Kopiere sie auf einen anderen Rechner oder einen USB-Stick. Hast du evcc mit dem Install-Script eingerichtet, liegt dessen Einrichtung im Ordner `evcc` daneben. Schreib dann `data evcc` statt `data`, damit sie mitgesichert wird.

**Wiederherstellen in der App:** Unter **Mehr → Daten & Sicherung** wählst du bei **Sicherung wiederherstellen** die Datei aus der App aus. Dafür musst du angemeldet sein. OpenAmpere prüft die Datei, behält dein Passwort und die gespeicherten Zugangsdaten und startet danach neu. Die bisherige Datenbank bleibt als Kopie im Ordner `data` erhalten.

**Ganzen Datenordner zurückholen:** Hast du den Ordner `data` wie oben gesichert, kopierst du ihn von Hand zurück. Die Datei muss dafür in deinem Benutzerordner auf dem Server liegen:

```bash
cd /opt/openampere
sudo docker compose stop openampere
sudo mv data data-alt
sudo tar xzf ~/openampere-sicherung.tar.gz
sudo docker compose start openampere
```

Der bisherige Datenordner bleibt als `data-alt` erhalten. Läuft alles wieder, kannst du ihn mit `sudo rm -r /opt/openampere/data-alt` löschen.

## Umziehen auf einen neuen Rechner

Du nimmst den ganzen Installationsordner mit. Darin liegen der Datenordner `data` mit `secret.key` und, falls eingerichtet, evcc und Tailscale. So bleiben Verlauf, Einstellungen und gespeicherte Passwörter erhalten.

1. **Auf dem alten Rechner** OpenAmpere beenden und den Ordner einpacken:
   ```bash
   cd /opt/openampere
   sudo docker compose down
   sudo tar czf ~/openampere-umzug.tar.gz -C /opt openampere
   ```
2. Die Datei `openampere-umzug.tar.gz` auf den neuen Rechner kopieren, etwa mit einem USB-Stick oder mit `scp ~/openampere-umzug.tar.gz <benutzer>@<neuer-rechner>:`.
3. **Auf dem neuen Rechner** (Linux mit 64-Bit-System) den Ordner auspacken und das Install-Script ausführen. Es erkennt die vorhandene Installation, installiert bei Bedarf Docker und startet OpenAmpere mit deinen bisherigen Antworten:
   ```bash
   sudo tar xzf ~/openampere-umzug.tar.gz -C /opt
   curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash
   ```
4. Die App unter der Adresse öffnen, die das Script am Ende anzeigt. Hat der neue Rechner eine andere IP-Adresse, brauchen auch das Lesezeichen auf dem Handy und verbundene Apps wie Home Assistant die neue Adresse.

Starte OpenAmpere auf dem alten Rechner danach nicht wieder. Sonst sprechen zwei Programme mit dem Wechselrichter und stören sich gegenseitig.

## Deinstallieren

**Vorher bedenken:** Einstellungen, die OpenAmpere am Wechselrichter geändert hat, etwa Notstrom-Reserve, Ladegrenzen, Betriebsmodus oder Einspeisebegrenzung, speichert der Wechselrichter selbst. Sie bleiben auch nach dem Deinstallieren aktiv, siehe [Sicherheit bei Steuerfunktionen](#sicherheit-bei-steuerfunktionen). Willst du die früheren Werte zurück, stelle sie vorher in der App wieder ein. Welche Werte vorher galten, steht unter **Mehr → Steuerung und Protokoll**. Nutzt du **Laden aus dem Netz**, schalte es vorher aus und warte ein paar Minuten, bis OpenAmpere die Fernsteuerung des Wechselrichters wieder freigegeben hat.

Willst du deine Daten behalten, lade vorher eine Sicherung herunter (siehe [Sichern und Wiederherstellen](#sichern-und-wiederherstellen)). Dann auf dem Server im Terminal:

```bash
cd /opt/openampere
sudo docker compose down
cd /
sudo rm -r /opt/openampere
```

Das beendet OpenAmpere mit allen Helfern und löscht den Ordner mit allen Messwerten. Docker selbst bleibt installiert. Hast du Tailscale genutzt, entferne den Rechner danach auch in der Tailscale-Verwaltung. In Home Assistant entfernst du die Integration OpenAmpere.

## Fehlerbehebung

Viele Hinweise zeigt schon der Einrichtungsassistent unter **Gerät wird nicht gefunden?**. Hilft das nicht, findest du hier die häufigsten Ursachen. Wenn du Hilfe brauchst, [melde ein Problem](https://github.com/Gr33ndev/OpenAmpere/issues) und hänge einen Bericht aus **Mehr → Diagnose** an.

### Meldungen des Servers ansehen

Was OpenAmpere gerade tut und welche Fehler auftreten, steht in den Meldungen des Servers:

```bash
cd /opt/openampere && sudo docker compose logs --tail 100 openampere
```

Mit `sudo docker compose logs -f openampere` laufen neue Meldungen fortlaufend mit, beenden mit Strg+C. Bevor du Meldungen öffentlich teilst, entferne IP-Adressen, Seriennummern und andere Angaben zu deiner Anlage.

### Der Wechselrichter wird nicht gefunden

- **Netzwerkkabel:** Der Wechselrichter braucht eine Verbindung ins Heimnetz, meist per Kabel am LAN-Anschluss. Ein reiner Cloud-WLAN-Stick reicht oft nicht.
- **IP-Adresse von Hand eingeben:** Du findest sie in der Geräteliste deines Routers (FRITZ!Box: Heimnetz → Netzwerk) oder im Menü am Display des Wechselrichters. Vergib ihm im Router am besten eine feste Adresse.
- **Modbus TCP muss eingeschaltet sein**, üblich ist Port 502. Bei den von EKD verkauften Speichern ist das schon der Fall. Siehe auch [Modbus TCP ist nicht eingeschaltet](#modbus-tcp-ist-nicht-eingeschaltet).
- **Gleiches Netz:** Server und Wechselrichter müssen im selben Heimnetz hängen. Ein Gastnetz trennt die Geräte voneinander.

### „Das Gerät lehnt die Verbindung ab“

Unter der Adresse antwortet ein Gerät, lässt aber keine Modbus-Verbindung zu. Meist ist Modbus TCP nicht eingeschaltet oder der Port stimmt nicht (üblich ist 502, bei einem Modbus-Proxy der Port des Proxys). Es kann auch sein, dass ein anderer Energiemanager alle Verbindungen belegt, siehe [unten](#ein-anderer-energiemanager-nutzt-die-verbindung).

### „Kein unterstütztes Gerät erkannt“: Geräteadresse prüfen

Das Gerät ist erreichbar, antwortet aber nicht wie erwartet. Meist stimmt die Geräteadresse nicht. Normalerweise probiert OpenAmpere alle bekannten Adressen selbst. Klappt das nicht, trage die Adresse im Einrichtungsassistenten unter **Erweitert: Gerätetyp, Port, Geräteadresse** oder unter **Mehr → Verbindung** ein:
- FoxESS H3 (auch als „Ampere.StoragePro E3“ verkauft): Geräteadresse 247.
- SAJ H2 / HS2 (ältere „Ampere.StoragePro“): Geräteadresse 1 oder 2, je nach Kommunikationsmodul.

Hängt ein Modbus-Proxy dazwischen, gilt die Geräteadresse, die im Proxy eingestellt ist.

### Ein anderer Energiemanager nutzt die Verbindung

Der Wechselrichter erlaubt nur wenige gleichzeitige Modbus-Verbindungen. Hängt noch ein anderer Energiemanager am Wechselrichter, zum Beispiel die bisherige Smartbox, sind eventuell alle belegt. Dann bricht die Verbindung von OpenAmpere oder vom anderen Gerät immer wieder ab. Abhilfe: In der App unter **Mehr → Verbindung → Erweitert** bei **Verbindung** die Einstellung **Pro Abfrage** wählen und speichern. OpenAmpere verbindet sich dann für jede Abfrage neu und gibt den Zugang danach wieder frei. Mehr dazu unter [Betrieb hinter einem Modbus-Proxy](#betrieb-hinter-einem-modbus-proxy).

### Modbus TCP ist nicht eingeschaltet

Bei den von EKD verkauften Speichern ist Modbus TCP schon eingeschaltet. Bei anderen Geräten schaltet es der Installationsbetrieb ein. Gibt es den Betrieb nicht mehr, frag einen anderen Elektro- oder Solarfachbetrieb in deiner Nähe oder den Kundendienst des Herstellers (FoxESS oder SAJ) mit der Seriennummer deines Geräts. Ändere im Service-Menü des Wechselrichters nichts, wenn du dir nicht sicher bist, was die Einstellung bewirkt.

### „32-Bit-System erkannt“

OpenAmpere braucht ein 64-Bit-System. Ob deines eines ist, zeigt der Befehl `uname -m`: `aarch64` oder `x86_64` passen, `armv7l` oder `armv6l` bedeuten 32 Bit. Auf einem Raspberry Pi 3, 4, 5 oder Zero 2 W spielst du mit dem Raspberry Pi Imager „Raspberry Pi OS (64-bit)“ auf die Speicherkarte und installierst OpenAmpere danach neu. Das löscht die Speicherkarte, sichere vorher alles, was du behalten willst. Ältere Modelle können kein 64-Bit-System ausführen.

### Der Port ist belegt

Das Install-Script sucht sich selbst einen freien Port zwischen 8080 und 8099. Belegt später ein anderes Programm diesen Port, startet OpenAmpere nicht, und in den Meldungen des Servers steht „address already in use“. Gib OpenAmpere dann einen anderen Port: Öffne `/opt/openampere/docker-compose.yml`, zum Beispiel mit `sudo nano /opt/openampere/docker-compose.yml`. Trage beim Dienst `openampere` unter `environment:` mit derselben Einrückung wie `TZ` eine Zeile wie `OPENAMPERE_SERVER_PORT: "8081"` ein. Steht dort schon eine solche Zeile, ändere nur die Zahl. Führe danach das Install-Script erneut aus. Es übernimmt den neuen Port und startet OpenAmpere neu. Die App öffnest du dann unter der neuen Adresse.

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

Die Python-Abhängigkeiten sind in `requirements.lock` (Docker-Image, mit Prüfsummen) und `requirements-dev.lock` (Entwicklung, CI) fest versioniert. Erzeugt werden beide mit [uv](https://docs.astral.sh/uv/) aus `pyproject.toml`:

```bash
scripts/deps.sh
```

Das erzeugt auch die Lizenzliste (`THIRD_PARTY_LICENSES.md` und die Liste in der App), alles gehört in denselben Commit. Fehlt etwas, etwa in Dependabot-PRs, erzeugt der Workflow `dependencies.yml` die Dateien neu und committet sie auf denselben Branch. Einmal pro Woche hebt er alle Python-Abhängigkeiten auf die neuesten erlaubten Versionen und öffnet dafür einen Pull Request vom Branch `deps/python-updates`. Die CI auf GitHub prüft Tests, Web-Build, Docker-Build und ob die Lizenzliste aktuell ist.

Projektseite mit Demo: Die Startseite liegt in `site/`, die Demo ist die normale Web-App, gebaut mit `VITE_DEMO=1`. Sie simuliert eine Anlage im Browser (`web/src/demo/`) und schickt nichts an einen Server. Lokal bauen und ansehen:

```bash
scripts/build-site.sh
```

```bash
python3 -m http.server 8090 -d _site
```

Der Workflow `pages.yml` veröffentlicht die Seite auf GitHub Pages (einmalig unter Settings → Pages die Quelle „GitHub Actions“ wählen). Ein Versions-Tag (`v…`) veröffentlicht das Image für x86 und ARM unter `ghcr.io`.

## Sicherheit bei Steuerfunktionen

Alles, was auf den Wechselrichter schreibt, ist ab Werk **aus**. Freigegeben wird es in der App unter **Mehr → Steuerung und Protokoll**, mit Sicherheitsabfrage:
- Nach der Freigabe läuft die Steuerung zunächst im **Testmodus**. Änderungen werden dann nur protokolliert.
- Erst wenn man den Testmodus ausdrücklich beendet, werden Werte an den Wechselrichter gesendet.
- Jede Änderung landet mit altem und neuem Wert im Protokoll und wird nach dem Schreiben vom Gerät zurückgelesen.

Einstellungen wie Ladegrenzen, Betriebsmodus oder Einspeisebegrenzung speichert der Wechselrichter selbst. Sie bleiben aktiv, auch wenn OpenAmpere nicht läuft oder deinstalliert wird. Wer etwas zurücknehmen will, muss es in der App (oder beim Installationsbetrieb) wieder ändern.

Andere Apps wie [Home Assistant](docs/homeassistant.de.md) steuern nur über einen eigenen Zugang mit der Berechtigung
„Lesen + Steuern“ und nur, solange die Steuerung freigegeben ist. Den Hauptschalter, den Testmodus und die
Einspeisebegrenzung können sie nicht ändern, Speicher-Einstellungen höchstens sechsmal pro Stunde.

Die Register zum Schreiben stammen aus der Dokumentation der Community und sind noch nicht an jeder Gerätevariante geprüft. Deshalb liest OpenAmpere jeden geschriebenen Wert zurück und meldet Abweichungen.

## Hintergrund & rechtliche Hinweise

**Wie OpenAmpere entstanden ist:**
- Die Energiekonzepte Deutschland GmbH (EKD) und weitere Gesellschaften der Gruppe haben Anfang Oktober 2026 Insolvenz beantragt. Die Verfahren laufen beim Amtsgericht Leipzig, die amtlichen Bekanntmachungen stehen unter [insolvenzbekanntmachungen.de](https://neu.insolvenzbekanntmachungen.de/ap/suche.jsf) (Gericht Leipzig, Aktenzeichen eingeben):
  - Energiekonzepte Deutschland GmbH: 401 IN 2082/26
  - AMPERE German Electric Innovation GmbH: 401 IN 2085/26
  - EKD Montage GmbH: 401 IN 2100/26
  - Energiekonzepte Deutschland Holding GmbH: 401 IN 2101/26
  - Energiekonzepte Deutschland Investorenholding GmbH: 401 IN 2107/26
  - Energiekonzepte Deutschland PV-Montage GmbH: 401 IN 2110/26
  - ES Energiesysteme GmbH: 401 IN 2111/26
- Damit sind alle sieben Gesellschaften amtlich veröffentlicht, die laut [Handelsblatt](https://www.handelsblatt.com/unternehmen/energie/solarenergie-solarspezialist-energiekonzepte-deutschland-meldet-insolvenz-an/100258988.html) betroffen sein sollen (Stand 6. Oktober 2026).
- Laut EKD läuft der Geschäftsbetrieb uneingeschränkt weiter, Anlaufstelle für Kunden bleibt der Kundenservice ([pv magazine](https://www.pv-magazine.de/2026/10/02/energiekonzepte-deutschland-stellt-insolvenzantrag/)).
- Die App „Ampere.IQ“ funktioniert nur über Server von EKD. Wie es damit weitergeht, ist offen. Sollte das Insolvenzverfahren dazu führen, dass diese Server abgeschaltet werden, läuft OpenAmpere einfach weiter: Es spricht direkt im Heimnetz mit dem Wechselrichter und braucht keinen Server von EKD.
- OpenAmpere entstand als lokale Alternative von Betroffenen für Betroffene.

**Warum es mit EKD-Anlagen funktioniert:** OpenAmpere spricht direkt mit den verbauten Wechselrichtern. Die von EKD als „Ampere.StoragePro E3“ vertriebenen Speicher basieren auf der FoxESS-H3-Serie, die älteren „Ampere.StoragePro“ auf SAJ H2/HS2. OpenAmpere funktioniert genauso mit diesen Geräten aus anderen Quellen.

**Keine Verbindung zu EKD oder den Herstellern:** OpenAmpere ist ein unabhängiges, inoffizielles Community-Projekt. Es steht in keinerlei Verbindung zur Energiekonzepte Deutschland GmbH, zu deren Insolvenzverwaltung, zu FoxESS, SAJ oder Kiwigrid. Es wurde von ihnen weder beauftragt noch autorisiert oder unterstützt.

**Marken:** „AMPERE“, „Ampere.IQ“, „Ampere.StoragePro“ sowie alle weiteren genannten Produkt- und Firmennamen sind Marken oder Bezeichnungen ihrer jeweiligen Inhaber. Alle Rechte daran liegen selbstverständlich bei diesen. Sie werden hier ausschließlich genannt, um zu beschreiben, mit welchen Geräten OpenAmpere zusammenarbeitet. Der Projektname bezieht sich auf die physikalische Einheit Ampere.

**Kein fremder Code:** OpenAmpere enthält keinen Code, keine Grafiken und keine Texte der Ampere.IQ-App. Der Cloud-Import nutzt ausschließlich die Kunden-API, die EKD für Kunden unter developer.ekd-solar.de beschrieben hat, mit dem persönlichen Schlüssel aus der App des jeweiligen Nutzers.

**Nutzung auf eigene Verantwortung:** OpenAmpere ist ein kostenloses Gemeinschaftsprojekt ohne Gewähr. Es ersetzt keinen Elektrofachbetrieb. Steuerfunktionen sind ab Werk ausgeschaltet. Wer Einstellungen am Wechselrichter ändert, insbesondere die Einspeisebegrenzung, ist selbst für die Einhaltung der Netzanschlussbedingungen verantwortlich. Ungeeignete Einstellungen können den Speicher belasten und Garantie- oder Gewährleistungsansprüche gegenüber Hersteller, Händler oder Insolvenzverwalter gefährden. Notiere die bisherigen Werte, bevor du etwas änderst. Die Hinweise in der App sind keine Rechtsberatung.

## Mitmachen

Fehler, Gerätediagnosen und Ideen sind willkommen – auch ohne Programmierkenntnisse. Erst ein Issue, dann der Pull Request; Details (englisch) in [CONTRIBUTING.md](CONTRIBUTING.md).

## Lizenz

[MIT](LICENSE). Hinweise zu verwendeten Quellen und Marken stehen in [NOTICE](NOTICE), die Lizenzen aller mitgelieferten Open-Source-Komponenten in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). Diese Datei wird mit `scripts/third_party_licenses.py` erzeugt. Bitte neu erzeugen, wenn sich Abhängigkeiten ändern.
