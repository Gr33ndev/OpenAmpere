# Wallbox mit evcc

English: [evcc.md](evcc.md)

OpenAmpere steuert Wallboxen nicht selbst. Das übernimmt [evcc](https://evcc.io), ein eigenständiges
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
  Testtoken läuft oder das Token bald abläuft, und sagt es, wenn evcc die Wallbox wegen eines fehlenden Tokens nicht
  startet.

Wir empfehlen, evcc zu unterstützen. OpenAmpere nutzt nur die offene Schnittstelle von evcc und hat keinen Einfluss
auf dessen Bedingungen.

## Ohne evcc (Vorschau)

Für einige Wallboxen bereitet OpenAmpere einen direkten Weg vor, zuerst für den FoxESS AC EV Charger (#318). Unter
**Mehr → Verbindung → Wallbox** wählst du **direkt (Vorschau)**: OpenAmpere prüft die Wallbox nur lesend, in der
Steuerungsart, in der sie gerade steht, und zeigt, was sie antwortet. An der Wallbox wird nichts geändert, und steuern
kann OpenAmpere sie so noch nicht. Das Ergebnis kommt in den Diagnosebericht (**Mehr → Diagnose**); wenn du ihn teilst,
hilft das, die Register an echten Geräten zu bestätigen. Für andere Wallboxen liest **Mehr → Diagnose → Gerät
auslesen** beliebige Register.

Wahlweise kann die Prüfung auch per **OCPP 1.6J** zuhören (#328): Starte dort den Zuhörer und trage die angezeigte
Adresse in der App des Herstellers als OCPP-Server ein. OpenAmpere antwortet der Wallbox mit „Pending“ und fragt nur
ihre Einstellungen ab; es startet und stoppt kein Laden. Solange die Wallbox auf OCPP steht, steuern sie weder die App
des Herstellers noch evcc, und ohne Ladeprofil lädt sie ohne Begrenzung: Stell sie danach wieder zurück. In der
Home-Assistant-App braucht die Wallbox die direkte Adresse (der direkte Zugriff per IP muss an sein).

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
Herstellers den Ladestand ab, meist nur solange das Auto angesteckt ist. Damit kann OpenAmpere mehr:

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

## Grenzen

- Manche Geräte sind in evcc nur mit einem [Sponsoring](https://docs.evcc.io/docs/sponsorship) nutzbar. Das ist
  Sache von evcc und unterstützt dessen Entwicklung.
- Den Heizstab von my-PV steuert OpenAmpere selbst (Mehr → Verbindung → Heizstab und weitere Geräte, bedient unter „Geräte“). Ist er stattdessen in evcc
  eingerichtet, zeigt OpenAmpere ihn als Ladepunkt von evcc an.
- Die interne Schnittstelle von evcc kann sich zwischen Versionen ändern. OpenAmpere liest sie fehlertolerant und
  verwendet Befehle, die alte und neue evcc-Versionen verstehen.
