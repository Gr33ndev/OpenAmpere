# OpenAmpere als Home-Assistant-App

*English: [ha-app/DOCS.md](../ha-app/DOCS.md)*

Läuft bei dir **Home Assistant OS**, zum Beispiel auf einem Home Assistant Green oder einem Raspberry Pi? Dann kannst
du OpenAmpere als App direkt in Home Assistant installieren. OpenAmpere läuft dann neben Home Assistant und öffnet sich
in der Seitenleiste. Einen eigenen Rechner oder Docker brauchst du dafür nicht.

Die App ist neu und als **experimentell** gekennzeichnet. Wenn etwas nicht klappt, schreib bitte ein
[Issue](https://github.com/Gr33ndev/OpenAmpere/issues).

> **Du hast OpenAmpere schon mit Docker installiert?** Lies zuerst [Umzug von Docker](#umzug-von-docker). Es darf nur
> ein OpenAmpere mit dem Wechselrichter sprechen.

## Installation

1. **Repository hinzufügen**, [mit einem Klick](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FGr33ndev%2FOpenAmpere) oder von Hand: In Home Assistant **Einstellungen → Apps → App
   installieren** öffnen, dann **⋮ → Repositorys**, `https://github.com/Gr33ndev/OpenAmpere` eintragen und
   **Hinzufügen**.
2. Die App **OpenAmpere** **installieren**, **In der Seitenleiste anzeigen** einschalten und die App **starten**.
3. **OpenAmpere** in der Seitenleiste **öffnen**. Der Einrichtungsassistent sucht den Wechselrichter in deinem
   Heimnetz, oder du gibst seine IP-Adresse ein.

Am Wechselrichter muss Modbus TCP eingeschaltet sein. Bei den von EKD verkauften Speichern ist das schon der Fall,
sonst kann es dein Installationsbetrieb erledigen.

In der Seitenleiste brauchst du kein Passwort: Home Assistant hat schon geprüft, wer du bist. Einstellungen ändern und
den Wechselrichter steuern dürfen dort nur **Administratoren von Home Assistant**; andere Benutzer von Home Assistant
können nur ansehen. Den Eintrag in der Seitenleiste sehen nur Administratoren. Bei Änderungen in der Seitenleiste steht
im Protokoll (Mehr → Steuerung und Protokoll), welcher Benutzer von Home Assistant sie gemacht hat. Bei Änderungen über
die Home-Assistant-Integration steht dort stattdessen der Name des Zugangs (Mehr → Verbundene Apps), nicht der
Benutzer.

## Zugriff über die IP-Adresse

Außer über die Seitenleiste kannst du OpenAmpere direkt unter `http://<Adresse von Home Assistant>:8780` öffnen, zum
Beispiel auf dem Handy ohne Home Assistant oder für [evcc](https://evcc.io). Dafür braucht OpenAmpere ein Passwort. Lege
es in der Seitenleiste unter **Mehr → Zugriffsschutz** fest. Bis dahin ist der direkte Zugriff gesperrt. Hast du es
vergessen, legst du dort einfach ein neues fest, das alte brauchst du nicht.

Browser schicken ihre Anmelde-Cookies an jeden Port einer Adresse. Öffnest du OpenAmpere unter derselben Adresse wie
Home Assistant, bekommen auch Home Assistant und seine anderen Apps die Anmeldung von OpenAmpere. Öffne den direkten
Zugriff deshalb unter einer anderen Adresse als Home Assistant: Rufst du Home Assistant über seinen Namen auf (zum
Beispiel `homeassistant.local:8123`), öffne OpenAmpere über die IP-Adresse, oder umgekehrt. In der Seitenleiste spielt
das keine Rolle.

- **Port belegt oder anderer Port gewünscht:** Einstellungen → Apps → OpenAmpere → **Konfiguration → Netzwerk**. Ist
  Port 8780 schon von einer anderen App belegt, startet OpenAmpere nicht. Wähle dann dort einen anderen Port.
- **Port 8743** ist HTTPS für Apps auf anderen Geräten. Die Home-Assistant-Integration braucht ihn nicht.
- **Direkten Zugriff ausschalten:** dort beide Ports leeren.

## Home-Assistant-Integration

Entitäten in Home Assistant (Leistung, Energie fürs Energie-Dashboard, Ladestand und, wenn du es erlaubst, die
Steuerung von Speicher, Laden aus dem Netz und Heizstab) kommen von der Integration OpenAmpere. **Die App bringt sie mit
und installiert sie selbst, HACS brauchst du nicht.** Updates der App aktualisieren auch die Integration.

1. Nach dem ersten Start der App zeigt Home Assistant einen Hinweis: **Home Assistant einmal neu starten**
   (Einstellungen → System → Neu starten).
2. Danach steht unter **Einstellungen → Geräte & Dienste** **Entdeckt: OpenAmpere**. Bestätige das.
3. Home Assistant und OpenAmpere zeigen denselben 6-stelligen Code. Vergleiche ihn in OpenAmpere unter
   **Mehr → Verbundene Apps** und erlaube die Verbindung, nur lesen oder auch steuern. Bestätige den Code danach
   noch einmal in Home Assistant.

Steuer-Entitäten sind nur verfügbar, solange in OpenAmpere die Steuerung eingeschaltet ist (Mehr → Steuerung und
Protokoll). Mit **„Lesen + Steuern“** können alle Benutzer von Home Assistant und alle Automationen sie verwenden, nicht
nur Administratoren. Home Assistant kann Entitäten nicht auf bestimmte Benutzer beschränken. Willst du das nicht, wähle
**„Nur lesen“**.

Hattest du die Integration vorher über HACS installiert? Dann lässt die App sie in Ruhe und sagt das in einem Hinweis.
Entferne sie in HACS und starte die App neu, ab dann hält die App sie aktuell. Mehr dazu in der
[Anleitung zur Integration](homeassistant.de.md).

## Umzug von Docker

Es darf nur ein OpenAmpere mit dem Wechselrichter sprechen. Zwei stören sich gegenseitig.

1. **In der bisherigen Installation:** unter **Mehr → Daten & Sicherung** die Datensicherung herunterladen. Danach die
   Installation stoppen, im Terminal auf dem Server: `cd /opt/openampere && sudo docker compose down`
2. **In der App:** den Einrichtungsassistenten durchgehen und dann unter **Mehr → Daten & Sicherung → Sicherung
   wiederherstellen** die Datei auswählen. OpenAmpere startet neu, mit deinem Verlauf und deinen Einstellungen.
3. **Gespeicherte Zugangsdaten neu eintragen**, zum Beispiel für das Kundenportal des Netzbetreibers, ntfy, evcc oder
   den Ampere.IQ-Import. Aus Sicherheitsgründen stehen sie nicht in der heruntergeladenen Sicherung. Das gilt auch für
   den Local Key einer Wallbox, die OpenAmpere selbst steuert: In der Sicherung steht er nur verschlüsselt mit dem
   Schlüssel der alten Installation. Die Wallbox behält ihre Einstellungen und zeigt unter **Mehr → Verbindung →
   Wallbox** und auf der Seite **Geräte** „Local Key neu eintragen“, bis du den Schlüssel dort neu einträgst.
4. **Home-Assistant-Integration:** Hast du sie über HACS installiert, entferne sie in HACS (nicht unter Geräte &
   Dienste) und starte die App neu; die App installiert dann ihre eigene. Home Assistant erkennt dein OpenAmpere wieder
   und wechselt von selbst zur App. Es fragt nur einmal nach einer neuen Kopplung. Entitäten und ihr Verlauf bleiben
   erhalten.
5. **evcc:** Liest evcc die Zähler von OpenAmpere, kopiere die Konfiguration unter **Mehr → Verbindung → Wallbox** neu.
   Darin steht die neue Adresse.

## Sicherungen und Updates

- **Sicherungen** von Home Assistant enthalten OpenAmpere mit Verlauf, Einstellungen und gespeicherten Zugangsdaten.
  OpenAmpere schreibt dafür jedes Mal eine saubere Kopie seiner Datenbank. Wiederherstellen kannst du sie wie bei jeder
  anderen App. In so einer Sicherung steht auch der Schlüssel für die gespeicherten Zugangsdaten (`secret.key`). Wer
  die Sicherung öffnen kann, kann sie also lesen. Geschützt sind sie nur durch die Verschlüsselung der Sicherungen von
  Home Assistant: Bewahre deren Schlüssel sicher auf und gib Sicherungen nicht weiter.
- **Nach dem Wiederherstellen einer Sicherung** sind das Passwort und die Zugänge für Apps wieder so wie zur Zeit der
  Sicherung, und angemeldete Geräte melden sich neu an. Hast du seither das Passwort geändert oder unter **Mehr → Verbundene Apps**
  einen Zugang entfernt, mach das bitte noch einmal. OpenAmpere erinnert dich daran, bis du den Hinweis ausblendest.
- **Updates** kommen über Home Assistant: Einstellungen → Apps → OpenAmpere. Dort kannst du auch automatische Updates
  einschalten.
- **Von unterwegs** öffnest du OpenAmpere über den Fernzugriff von Home Assistant in der Seitenleiste, zum Beispiel mit
  Home Assistant Cloud oder der Tailscale-App für Home Assistant.

## Deinstallieren

Schalte vorher die Geräte aus, die OpenAmpere über ein Relais schaltet, zum Beispiel eine Wärmepumpe oder einen
Heizstab: Stelle sie auf der Seite **Geräte** auf **Aus**. Wenn OpenAmpere beendet wird, setzt es regelbare Geräte auf
0 W. Ein Relais, das es eingeschaltet hat, bleibt aber an, bis OpenAmpere wieder läuft. Das gilt auch, wenn du die App
nur stoppst.

Lade vorher unter **Mehr → Daten & Sicherung** eine Sicherung herunter, falls du zurückkommen möchtest: Beim
Deinstallieren der App werden ihre Daten gelöscht (Verlauf und Einstellungen). Dann die App unter Einstellungen → Apps →
OpenAmpere deinstallieren.

Home Assistant zeigt danach unter **Einstellungen → System → Reparaturen** den Hinweis „Die OpenAmpere-App wurde
entfernt“. Bestätige ihn, dann wird die Integration mit ihren Geräten und Entitäten entfernt, samt den Dateien, die die
App installiert hat. Der Verlauf der Entitäten bleibt in Home Assistant. Eine Integration, die du über HACS installiert
hast, bleibt; entferne sie in HACS.

## Probleme

- **Protokoll:** Einstellungen → Apps → OpenAmpere → **Protokoll**.
- **Der Wechselrichter wird nicht gefunden:** siehe [Fehlerbehebung](../README.de.md#fehlerbehebung).

## Hinweise

OpenAmpere ist ein unabhängiges Open-Source-Projekt. Es hat nichts mit Home Assistant, der Open Home Foundation oder
Nabu Casa zu tun, ebenso wenig mit der Energiekonzepte Deutschland GmbH (EKD), FoxESS, SAJ oder Kiwigrid. Die
Steuerfunktionen sind ab Werk ausgeschaltet. Wer Einstellungen am Wechselrichter ändert, ist selbst dafür
verantwortlich, die Netzanschlussbedingungen einzuhalten. Die Hinweise in der App sind keine Rechtsberatung. Mehr dazu
im [README](../README.de.md#hintergrund--rechtliche-hinweise).
