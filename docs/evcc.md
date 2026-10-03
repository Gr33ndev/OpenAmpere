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

1. evcc installieren, zum Beispiel mit Docker neben OpenAmpere (siehe unten) oder nach der
   [Anleitung von evcc](https://docs.evcc.io).
2. In evcc die Zähler von OpenAmpere eintragen. Die fertige Konfiguration mit der richtigen Adresse zeigt die App
   unter Geräte → Wallbox einrichten zum Kopieren an. Sie sieht so aus:

   ```yaml
   meters:
     - name: openampere_grid
       type: custom
       power:
         source: http
         uri: http://openampere:8080/api/evcc/site
         jq: .grid_power
       energy:
         source: http
         uri: http://openampere:8080/api/evcc/site
         jq: .grid_import_kwh
     - name: openampere_pv
       type: custom
       power:
         source: http
         uri: http://openampere:8080/api/evcc/site
         jq: .pv_power
     - name: openampere_battery
       type: custom
       power:
         source: http
         uri: http://openampere:8080/api/evcc/site
         jq: .battery_power
       soc:
         source: http
         uri: http://openampere:8080/api/evcc/site
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
4. In OpenAmpere unter Geräte → Wallbox einrichten die Adresse von evcc eintragen, z. B. `http://evcc:7070`. Ein Passwort ist nur
   nötig, wenn evcc für Änderungen eine Anmeldung verlangt.

## evcc mit Docker neben OpenAmpere

In der `docker-compose.yml` von OpenAmpere als zweiten Dienst ergänzen (Ausschnitt):

```yaml
  evcc:
    image: evcc/evcc:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./evcc/evcc.yaml:/etc/evcc.yaml
      - ./evcc/data:/root/.evcc
```

Mit `network_mode: host` erreicht evcc OpenAmpere unter `http://localhost:8080` und OpenAmpere evcc unter
`http://localhost:7070`. In der Konfiguration oben dann `localhost` statt `openampere` eintragen.

## Grenzen

- Manche Geräte sind in evcc nur mit einem [Sponsoring](https://docs.evcc.io/docs/sponsorship) nutzbar. Das ist
  Sache von evcc und unterstützt dessen Entwicklung.
- Den Heizstab von my-PV steuert OpenAmpere selbst (Geräte → Heizstab und weitere Geräte). Ist er stattdessen in evcc
  eingerichtet, zeigt OpenAmpere ihn als Ladepunkt von evcc an.
- Die interne Schnittstelle von evcc kann sich zwischen Versionen ändern. OpenAmpere liest sie fehlertolerant und
  verwendet Befehle, die alte und neue evcc-Versionen verstehen.
