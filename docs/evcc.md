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

## Einrichtung

evcc bekommt seine Messwerte von OpenAmpere. Deshalb zuerst OpenAmpere einrichten, dann evcc.

1. evcc installieren, am einfachsten als zweiten Container neben OpenAmpere (siehe unten). Läuft evcc schon,
   zum Beispiel auf einem anderen Rechner, kann es so bleiben.
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
