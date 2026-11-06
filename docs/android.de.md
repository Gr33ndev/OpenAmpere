English: [android.md](android.md)

# OpenAmpere auf einem Android-Tablet

Du hast keinen Linux-Rechner und kein Home Assistant, aber ein altes Android-Tablet? Dann kann OpenAmpere auf dem
Tablet laufen. Die App bringt alles mit: Sie liest deine Solaranlage rund um die Uhr, speichert die Messwerte auf dem
Tablet und zeigt dieselbe Oberfläche wie die anderen Wege. Handy und Computer im Heimnetz öffnen sie im Browser.

Die Android-App ist neu. Wenn etwas nicht klappt, schreib bitte ein
[Issue](https://github.com/Gr33ndev/OpenAmpere/issues).

> **OpenAmpere läuft bei dir schon mit Docker oder in Home Assistant?** Lies zuerst [Umzug](#umzug). Es darf nur ein
> OpenAmpere mit dem Wechselrichter sprechen.

## Was das Tablet braucht

- **Android 7.0 oder neuer** mit einem ARM-Prozessor (das haben fast alle Tablets, 32 oder 64 Bit).
- **Immer am Ladegerät und im WLAN zu Hause.** Das Tablet ist jetzt der Server: Ist es aus, leer oder außer Haus,
  liest OpenAmpere nichts. Der Bildschirm darf aus sein.
- Mindestens 500 MB freier Speicher für die App und die Messwerte.
- Am Wechselrichter muss Modbus TCP eingeschaltet sein. Bei den von EKD verkauften Ampere.StoragePro-Speichern ist das
  schon der Fall, sonst kann es dein Installationsbetrieb erledigen.

**Akku:** Ein Tablet, das jahrelang am Ladegerät hängt, kann einen aufgeblähten Akku bekommen. Schalte, wenn dein
Tablet das hat, das Schonen des Akkus ein (zum Beispiel „Akku schützen“ oder „Laden auf 85 % begrenzen“, je nach
Hersteller in den Akku-Einstellungen). Wird das Gehäuse dicker oder hebt sich der Bildschirm, nimm das Tablet vom
Strom und benutze es nicht weiter.

## Installation

1. **Herunterladen:** Öffne auf dem Tablet im Browser die
   [neueste Version von OpenAmpere](https://github.com/Gr33ndev/OpenAmpere/releases/latest) und tippe unter
   „Assets“ auf die Datei, die auf `.apk` endet (`openampere-<Version>.apk`).
2. **Installieren:** Tippe auf die heruntergeladene Datei. Android fragt beim ersten Mal, ob der Browser Apps
   installieren darf: Erlaube es in der Einstellung, die sich öffnet, und tippe dann auf **Installieren**.
   Google Play Protect kennt die App nicht und fragt eventuell nach. Tippe dann auf **Trotzdem installieren**.
3. **Öffnen:** Starte OpenAmpere. Die App fragt einmal, ob sie Benachrichtigungen zeigen darf (ab Android 13) und ob
   sie vom Energiesparen ausgenommen werden darf. **Erlaube beides.** Ohne die Ausnahme vom Energiesparen legt Android
   OpenAmpere schlafen und es fehlen Messwerte.
4. **Einrichten:** Lege ein Passwort fest. Der Einrichtungsassistent sucht dann den Wechselrichter in deinem
   Heimnetz, oder du gibst seine IP-Adresse ein.

Solange OpenAmpere läuft, zeigt Android eine dauerhafte Benachrichtigung „OpenAmpere“ mit dem Text „Liest deine
Solaranlage rund um die Uhr …“. Sie muss bleiben: Daran erkennt Android, dass die App gebraucht wird. Schalte in den
Benachrichtigungs-Einstellungen von OpenAmpere die Kategorie „OpenAmpere läuft“ nicht aus. Nach einem Neustart des Tablets startet OpenAmpere von selbst.

## Von anderen Geräten öffnen

Auf dem Handy oder Computer im selben WLAN öffnest du `http://<Adresse des Tablets>:8080`. Die Adresse des Tablets
steht in den WLAN-Einstellungen des Tablets (bei deinem WLAN unter „IP-Adresse“). Damit sie gleich bleibt, gib dem
Tablet in deinem Router eine feste Adresse, zum Beispiel in der FRITZ!Box unter **Heimnetz → Netzwerk** bei dem Gerät
„Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“. Auf dem iPhone kannst du die Seite zum
Home-Bildschirm hinzufügen.

Andere Apps wie [evcc](https://evcc.io) oder Home Assistant verbinden sich über die verschlüsselte Adresse
`https://<Adresse des Tablets>:8443`, wie auf den anderen Wegen.

## Damit es durchläuft

Manche Hersteller beenden Apps im Hintergrund trotz der Ausnahme vom Energiesparen. Dann fehlen im Verlauf Stunden. Prüfe
in diesem Fall:

- **Einstellungen → Apps → OpenAmpere → Akku:** „Nicht eingeschränkt“ (oder „Uneingeschränkt“).
- **Samsung:** Einstellungen → Akku → Hintergrund-Nutzungslimits: OpenAmpere unter „Nie schlafende Apps“ eintragen.
- **Xiaomi, Huawei und andere:** zusätzlich „Autostart“ für OpenAmpere erlauben.

Die Menüs heißen je nach Tablet etwas anders. Wische OpenAmpere nicht aus der Liste der zuletzt benutzten Apps weg,
wenn dein Tablet die App dann ganz beendet.

## Was auf dem Tablet anders ist

- **Updates** installiert die App selbst, siehe [Updates](#updates).
- **Zugriff von unterwegs:** Installiere die App [Tailscale](https://tailscale.com) auf dem Tablet und auf deinem Handy
  und melde dich in beiden mit demselben Konto an. Dann öffnest du auf dem Handy die Tailscale-Adresse des Tablets mit
  Port 8080. Den Tailscale-Helfer von Weg 1 gibt es auf dem Tablet nicht.
- **Lücken im Verlauf:** War das Tablet aus oder leer, fehlen für diese Zeit Messwerte. OpenAmpere wertet das nicht als
  Stromausfall. Die Tageswerte stimmen trotzdem, denn sie kommen aus den Zählern des Wechselrichters.
- **Ein anderes Netzwerk:** Nimmst du das Tablet mit, findet OpenAmpere unter der Adresse des Wechselrichters vielleicht
  ein anderes Gerät. Es erkennt das an der Seriennummer und liest und steuert dieses Gerät nicht. Zurück im Heimnetz
  geht es von selbst weiter.

## Steuerung, wenn das Tablet ausfällt

Steuert OpenAmpere den Wechselrichter dauerhaft, etwa beim Laden aus dem Netz, endet das von selbst, wenn das Tablet
ausfällt:

- **FoxESS:** OpenAmpere nutzt die Fernsteuerung des Wechselrichters mit einer Zeitgrenze von 3 Minuten. Danach kehrt
  der Wechselrichter zu seinem eigenen Betrieb zurück.
- **SAJ** (die älteren Ampere.StoragePro): OpenAmpere lädt über ein Ladezeitfenster im Wechselrichter, das nach
  höchstens 10 Minuten von selbst endet. Deine eigenen Werte für dieses Zeitfenster setzt OpenAmpere zurück, sobald es
  wieder läuft.

Einstellungen, die OpenAmpere dauerhaft geändert hat (zum Beispiel die Notstrom-Reserve), bleiben, bis
OpenAmpere wieder läuft.

Steuert OpenAmpere eine **FoxESS-Wallbox**, lädt sie bei einem Ausfall mit 6 A weiter, bis das Auto voll ist oder du
das Laden an der Wallbox beendest. Das ist bei einem Server genauso.

## Updates

Neue Versionen zeigt OpenAmpere wie auf den anderen Wegen an („Version … ist da“). Ein Tipp auf **Aktualisieren**
installiert sie, auf Wunsch auch nachts von selbst. Die App lädt die neue Version nur von GitHub, prüft ihre Prüfsumme und ihre Signatur
und installiert sie dann.

- **Beim ersten Update** musst du OpenAmpere einmal erlauben, Apps zu installieren: Eine Benachrichtigung führt zu der
  Einstellung. Danach fragt Android noch einmal nach („Update auf Version … bereit“, dann **Installieren**).
- **Ab Android 12** installiert OpenAmpere weitere Updates ohne Nachfrage. Auf älterem Android tippst du jedes Mal auf
  die Benachrichtigung und dann auf **Installieren**.
- Nach dem Update startet OpenAmpere von selbst wieder. War die App vorne, bringt dich ab Android 10 die
  Benachrichtigung „Version … ist installiert“ zurück zu ihr.

Zurück auf eine ältere Version geht auf Android nur durch Deinstallieren, und dabei gehen die Daten verloren. Lade
deshalb vor dem Deinstallieren eine Sicherung herunter. Möchtest du Updates lieber mit
[Obtainium](https://github.com/ImranR98/Obtainium) verfolgen, trage dort `https://github.com/Gr33ndev/OpenAmpere` ein.

## Home Assistant verbinden

Die Integration OpenAmpere für Home Assistant verbindet sich mit dem Tablet wie mit einem Server (Weg 1): Installiere
sie über HACS, füge sie hinzu und gib die Adresse des Tablets ein. Die Schritte stehen im README unter
[Home Assistant verbinden](../README.de.md#home-assistant-verbinden).

## Sichern

Unter **Mehr → Daten & Sicherung** lädt **Datensicherung herunterladen** die komplette Datenbank mit allen Messwerten
und Einstellungen herunter. Auf dem Tablet fragt Android, wo die Datei gespeichert werden soll. Lade die Sicherung
lieber von einem Computer im Heimnetz herunter und bewahre sie dort auf: Geht das Tablet kaputt, ist sonst auch die
Sicherung weg.

## Umzug

Von Docker oder der Home-Assistant-App auf das Tablet:

1. Am bisherigen OpenAmpere unter **Mehr → Daten & Sicherung** eine **Datensicherung herunterladen**.
2. Das bisherige OpenAmpere **beenden** (Docker: `sudo docker compose down` im Installationsordner, Home Assistant:
   die App stoppen).
3. Die App auf dem Tablet installieren und ein Passwort festlegen. Den Einrichtungsassistenten brauchst du nicht.
4. Unter **Mehr → Daten & Sicherung** bei **Sicherung wiederherstellen** die Datei auswählen.

Passwörter und Schlüssel für andere Dienste (evcc, ntfy, Postfach für E-Mails, Kundenportal, Local Key einer Wallbox) sind nicht in der
Sicherung; trage sie auf dem Tablet neu ein. Verbundene Apps wie Home Assistant brauchen die neue Adresse.

Umgekehrt geht es genauso: Sicherung auf dem Tablet herunterladen, die App beenden oder deinstallieren, Sicherung im
neuen OpenAmpere einspielen.

## Deinstallieren

**Vorher bedenken:** Einstellungen, die OpenAmpere am Wechselrichter geändert hat, bleiben dort aktiv, siehe
[Deinstallieren im README](../README.de.md#deinstallieren). Beim Deinstallieren der App löscht Android alle Messwerte.
Lade vorher eine Sicherung herunter, wenn du sie behalten willst.

## Herkunft prüfen

Jede Version ist mit dem Schlüssel des Projekts signiert. Android installiert ein Update nur, wenn es mit demselben
Schlüssel signiert ist wie die installierte App. Der SHA-256-Fingerabdruck des Zertifikats ist:

```
26952e8da7f7093cf8ea99622d1576c88d075896b851e8ba4ff792f109854886
```

Wer es von Hand prüfen will, braucht einen Computer mit dem Android SDK: `apksigner verify --print-certs
openampere-<version>.apk` muss bei „certificate SHA-256 digest“ diesen Wert zeigen. Außerdem hat jede Datei
eines Release einen signierten Herkunftsnachweis, den die [GitHub CLI](https://cli.github.com) prüft:

```bash
gh attestation verify openampere-<version>.apk --repo Gr33ndev/OpenAmpere \
  --signer-workflow Gr33ndev/OpenAmpere/.github/workflows/release.yml --source-ref refs/tags/v<version>
```

## Probleme

- **„Port 8080 wird schon von einer anderen App auf dem Tablet benutzt“:** Beende oder deinstalliere diese App und
  starte das Tablet neu, wie die Meldung sagt.
- **„OpenAmpere läuft nicht“:** OpenAmpere konnte mehrmals hintereinander nicht starten. Die App zeigt den Grund an,
  beim nächsten Öffnen versucht sie es noch einmal. Hilft ein Neustart des Tablets nicht, schreib bitte ein
  [Issue](https://github.com/Gr33ndev/OpenAmpere/issues) mit dem Text, den die App zeigt.
- **Der Wechselrichter wird nicht gefunden:** siehe [Fehlerbehebung im README](../README.de.md#fehlerbehebung).
