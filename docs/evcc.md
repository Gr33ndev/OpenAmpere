# Wallbox mit evcc

OpenAmpere steuert Wallboxen nicht selbst. Das übernimmt [evcc](https://evcc.io), ein eigenständiges
Open-Source-Projekt für Solarladen (MIT-Lizenz, [GitHub](https://github.com/evcc-io/evcc)). evcc unterstützt sehr
viele Wallboxen und Fahrzeuge, Ladepläne, dynamische Tarife und Ladevorgänge mit Abrechnung. Wir bauen das nicht
nach, sondern nutzen es. Danke an die evcc-Community!

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

Zeilen einkommentieren, dann `docker compose up -d`. evcc speichert seine Einstellungen im Ordner `evcc/` und ist
unter `http://<server-ip>:7070` erreichbar. Beide Container nutzen das Host-Netzwerk, so erreicht evcc OpenAmpere
unter `http://localhost:8080` und OpenAmpere evcc unter `http://localhost:7070`. Wer evcc lieber mit Datei
konfiguriert, bindet zusätzlich `./evcc.yaml:/etc/evcc.yaml` ein, siehe
[Docker-Anleitung von evcc](https://docs.evcc.io/en/installation/docker).

## Grenzen

- Manche Geräte sind in evcc nur mit einem [Sponsoring](https://docs.evcc.io/docs/sponsorship) nutzbar. Das ist
  Sache von evcc und unterstützt dessen Entwicklung.
- Den Heizstab von my-PV steuert OpenAmpere selbst (Mehr → Verbindung → Heizstab und weitere Geräte, bedient unter „Geräte“). Ist er stattdessen in evcc
  eingerichtet, zeigt OpenAmpere ihn als Ladepunkt von evcc an.
- Die interne Schnittstelle von evcc kann sich zwischen Versionen ändern. OpenAmpere liest sie fehlertolerant und
  verwendet Befehle, die alte und neue evcc-Versionen verstehen.
