# Wallbox mit evcc

English: [evcc.md](evcc.md)

Diese Seite beschreibt Wallboxen, die [evcc](https://evcc.io) steuert, ein eigenständiges
Open-Source-Projekt für Solarladen (MIT-Lizenz, [GitHub](https://github.com/evcc-io/evcc)). evcc unterstützt sehr
viele Wallboxen und Fahrzeuge, Ladepläne, dynamische Tarife und Ladevorgänge mit Abrechnung. Wir bauen das nicht
nach, sondern nutzen es. Danke an die evcc-Community!

## Kosten: das Sponsortoken von evcc

evcc ist freie Software, verlangt aber für viele kommerzielle Wallboxen ein **Sponsortoken**. So finanziert sich das
evcc-Projekt: ein jährlicher oder einmaliger Beitrag, aktuelle Preise auf der
[Sponsoring-Seite von evcc](https://docs.evcc.io/de/sponsorship). Open-Hardware-Wallboxen, Wallboxen von Herstellern,
die evcc unterstützen, Schaltsteckdosen und Plugins funktionieren ohne Token.

- **Braucht meine Wallbox eines?** Die [Wallbox-Liste von evcc](https://docs.evcc.io/de/chargers) kennzeichnet jede
  Wallbox mit „sponsoring“ (Token nötig) oder „sponsor free“ (ohne Token). Der Filter „sponsor free“ zeigt nur die
  ohne Token.
- **Testtoken:** evcc veröffentlicht auf der Sponsoring-Seite ein kostenloses Testtoken. Es ist zum Ausprobieren
  gedacht und gilt nur etwa zehn Tage. Danach startet evcc eine Wallbox, die ein Token braucht, nicht mehr. Ein neues
  Testtoken erscheint regelmäßig, aber alle paar Tage eines zu kopieren ist nicht als Dauerlösung gedacht.
- **Bezahltes Token:** hat ebenfalls ein Ablaufdatum; ein neues bekommst du auf
  [sponsor.evcc.io](https://sponsor.evcc.io), und evcc warnt 30 Tage vorher.
- **In OpenAmpere:** Unter **Geräte** und **Mehr → Verbindung → Wallbox** warnt OpenAmpere, solange evcc mit dem
  Testtoken läuft oder das Token bald abläuft, sagt, wenn das Token abgelaufen ist (#462), und sagt es, wenn evcc die Wallbox wegen eines fehlenden Tokens nicht
  startet. Liest OpenAmpere eine Wallbox direkt und hat evcc keinen Ladepunkt, sagt es stattdessen: Ist das die
  Wallbox, die OpenAmpere direkt liest, braucht sie kein Token und gehört in evcc gelöscht; steuert evcc eine andere
  Wallbox, trag ein gültiges Token ein (#535). Siehe [Wallbox ohne evcc](wallbox-direct.de.md).

Wir empfehlen, evcc zu unterstützen. OpenAmpere nutzt nur die offene Schnittstelle von evcc und hat keinen Einfluss
auf dessen Bedingungen.

## Wie die beiden zusammenarbeiten

```
Wechselrichter ── Modbus TCP ──▶ OpenAmpere ── /api/evcc/site ──▶ evcc ──▶ Wallbox
                                     ▲                               │
                                     └──── REST-API von evcc ◀────────┘
                                     Anzeige und Bedienung in der App
```

- **evcc bekommt die Messwerte von OpenAmpere.** Netz, Solar und Speicher liest evcc unter `/api/evcc/site`. So
  braucht evcc keine eigene Verbindung zum Wechselrichter. Viele Wechselrichter erlauben nur wenige gleichzeitige
  Modbus-Verbindungen.
- **OpenAmpere zeigt und bedient die Ladepunkte.** Lademodus (Aus, Solar, Min + Solar, Sofort), Ladeziel, Ladeplan
  bis zu einer Uhrzeit stehen unter „Geräte“. Energiefluss, Tageswerte und Auswertung zeigen das Laden ebenfalls,
  die Ladevorgänge findest du in der Auswertung.
- **Überschuss wird aufgeteilt.** Unter „Geräte“ legst du in einer Liste fest, wer Sonnenstrom zuerst bekommt:
  Speicher (bis zu einem Ladestand), Wallbox, Heizstab und weitere Geräte. Steht der Speicher vor der Wallbox,
  überträgt OpenAmpere den Ladestand als „Vorrang Hausspeicher“ (priority SoC) an evcc.

## Das Auto in evcc

Viele Autos lassen sich in evcc als Fahrzeug einrichten, zum Beispiel VW, Cupra, Skoda, Tesla, BMW oder Renault
([Liste bei evcc](https://docs.evcc.io/de/vehicles), manche nur mit Sponsoring). evcc fragt dann über die Cloud des
Herstellers den Ladestand ab, meist nur solange das Auto angesteckt ist. Für Autos des Volkswagen-Konzerns (VW, Audi,
Skoda, SEAT, CUPRA) geht das jetzt über das EU-Data-Act-Portal des Konzerns. Es muss einmal im Browser eingerichtet
werden und liefert Werte, die mindestens 15 Minuten alt sind; OpenAmpere kann dieses Portal auch selbst lesen, siehe
[car.de.md](car.de.md). Damit kann OpenAmpere mehr:

- **Ladestand und Reichweite** im Energiefluss und auf der Wallbox-Karte, dazu „Ziel erreicht um 15:40 Uhr“.
- **Laden bis 80 %** und Ladepläne wie „bis 7:00 Uhr auf 80 %“. Ohne Fahrzeug kennt evcc nur die geladenen kWh.
- **Mindestladung:** Bis zu diesem Ladestand lädt das Auto sofort, auch mit Netzstrom, danach gilt der Lademodus.
  Das ist eine Einstellung des Fahrzeugs in evcc, OpenAmpere setzt sie unter „Geräte“.
- **Auswertung „Auto“:** evcc speichert bei jedem Ladevorgang den Kilometerstand. OpenAmpere rechnet daraus gefahrene
  Kilometer, Kilometer mit Sonnenstrom, Verbrauch pro 100 km und Kosten pro 100 km, für 30 Tage, 12 Monate oder
  insgesamt. Sonnenstrom zählt dabei mit der entgangenen Einspeisevergütung, Netzstrom mit dem Preis aus deinem
  Stromtarif in OpenAmpere. Lädst du auch unterwegs, fehlt diese Energie, und der Verbrauch wirkt zu niedrig.

## Einrichtung

evcc bekommt seine Messwerte von OpenAmpere. Deshalb zuerst OpenAmpere einrichten, dann evcc.

1. evcc installieren. Das Install-Script von OpenAmpere erledigt das, wenn man die Frage nach der Wallbox
   bejaht. Bei einer Installation von Hand siehe unten. Läuft evcc schon, zum Beispiel auf einem anderen
   Rechner, kann es so bleiben.
2. In evcc die Zähler von OpenAmpere eintragen. Die fertige Konfiguration mit der richtigen Adresse zeigt die App
   unter Mehr → Verbindung → Wallbox zum Kopieren an. In der Weboberfläche von evcc fügt man sie als
   benutzerdefiniertes Gerät ein, alternativ in die `evcc.yaml`. Sie sieht so aus, statt `localhost` steht dort die
   Adresse, unter der du OpenAmpere geöffnet hast:

   ```yaml
   meters:
     - name: openampere_grid
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .grid_power
       energy:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .grid_import_kwh
     - name: openampere_pv
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .pv_power
     - name: openampere_battery
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .battery_power
       soc:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .battery_soc

   site:
     meters:
       grid: openampere_grid
       pv: [openampere_pv]
       battery: [openampere_battery]
   ```

   Die Vorzeichen passen ohne Umrechnung: Netz positiv bei Bezug, Speicher positiv beim Entladen. Hat OpenAmpere
   keine aktuellen Werte, antwortet die Adresse mit einem Fehler, und evcc lädt nicht auf Basis alter Werte.
3. Wallbox und Fahrzeug direkt in evcc einrichten.
4. In OpenAmpere unter Mehr → Verbindung → Wallbox die Adresse von evcc eintragen, auf demselben Rechner
   `http://localhost:7070`. Ein Passwort ist nur nötig, wenn evcc für Änderungen eine Anmeldung verlangt.

## evcc mit Docker neben OpenAmpere

Die `docker-compose.yml` von OpenAmpere enthält evcc schon als auskommentierten zweiten Dienst:

```yaml
  evcc:
    image: evcc/evcc:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./evcc:/root/.evcc
```

Zuerst den Ordner `evcc/` so anlegen, dass nur root ihn lesen kann, denn evcc speichert dort auch Passwörter:

```bash
sudo mkdir -p evcc && sudo chown root:root evcc && sudo chmod 700 evcc
```

Dann die Zeilen einkommentieren und `docker compose up -d` ausführen. evcc speichert seine Einstellungen im Ordner
`evcc/` und ist unter `http://<server-ip>:7070` erreichbar. Dort gleich als Erstes ein Passwort für evcc festlegen.
Bis dahin kann jeder im Heimnetz das Passwort selbst festlegen und evcc verändern. Beide Container nutzen das
Host-Netzwerk, so erreicht evcc OpenAmpere unter `http://localhost:8080` und OpenAmpere evcc unter
`http://localhost:7070`. Wer evcc lieber mit Datei konfiguriert, bindet zusätzlich `./evcc.yaml:/etc/evcc.yaml` ein,
siehe [Docker-Anleitung von evcc](https://docs.evcc.io/en/installation/docker).

## FoxESS AC EV Charger (auch die Wallbox von EKD-Anlagen)

Die Wallbox, die EKD mit seinen Ampere.IQ-Anlagen verkauft hat, ist ein FoxESS AC EV Charger: Im Router und in der
Suche von OpenAmpere heißt sie `EVC-…`. In evcc richtest du sie mit der Vorlage **FoxESS** (`foxess-evc`) ein. Dafür
braucht evcc ein Sponsor-Token.

- Stell in der App **Fox Switch** die Steuerungsart der Wallbox auf **Modbus TCP**. In der Standard-Einstellung
  lässt sich die Wallbox laut FoxESS über Modbus nur lesen, evcc kann sie dann nicht steuern. Manche Firmware lehnt
  sogar das Lesen ab („Gerät beschäftigt“).
- `connection reset by peer` in evcc heißt: Die Wallbox hat die Verbindung sofort wieder geschlossen. Woran das
  liegt, ist noch nicht geklärt. Möglich sind eine andere Steuerungsart als Modbus TCP oder ein anderes Programm, das
  schon ihre Verbindung hält (OpenAmpere geht davon aus, dass sie nur eine erlaubt). Lass nur ein Programm die
  Wallbox lesen.
- Register-Listen anderer Wallbox-Hersteller (zum Beispiel die Register 100, 300 und 400) passen nicht zu dieser
  Wallbox. Bei einem Besitzer lief ein „benutzerdefiniertes“ Gerät in evcc mit diesen Registern nur in
  Zeitüberschreitungen.

Ohne evcc kann OpenAmpere die Wallbox nur lesend prüfen, siehe
[wallbox-direct.de.md](wallbox-direct.de.md#andere-wallboxen-prüfen-nur-lesen).

## Nachricht aufs Handy beim Laden

Unter **Mehr → Benachrichtigungen** schickt dir **Laden der Wallbox** eine Nachricht, wenn an einem Ladepunkt von evcc
ein Auto zu laden beginnt und wenn das Laden endet, mit der Energie und dem Anteil Solarstrom seit dem Anstecken, wie
evcc sie zählt. Eine Pause von weniger als 15 Minuten gilt nicht als Ende. Ladepunkte für eine Wärmepumpe oder einen Heizstab meldet OpenAmpere
nicht.

## Grenzen

- Manche Geräte sind in evcc nur mit einem [Sponsoring](https://docs.evcc.io/docs/sponsorship) nutzbar. Das ist
  Sache von evcc und unterstützt dessen Entwicklung.
- Den Heizstab von my-PV steuert OpenAmpere selbst (Mehr → Verbindung → Heizstab und weitere Geräte, bedient unter „Geräte“). Ist er stattdessen in evcc
  eingerichtet, zeigt OpenAmpere ihn als Ladepunkt von evcc an.
- Die interne Schnittstelle von evcc kann sich zwischen Versionen ändern. OpenAmpere liest sie fehlertolerant und
  verwendet Befehle, die alte und neue evcc-Versionen verstehen.
