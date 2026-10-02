# Export aus der EKD-Cloud

Sichert den kompletten Verlauf einer Anlage aus der EKD-Cloud (App „Ampere.IQ“), solange diese noch läuft.

> Inoffizielles Werkzeug: OpenAmpere hat nichts mit der Energiekonzepte Deutschland GmbH (EKD) zu tun und wurde von ihr weder beauftragt noch autorisiert. Das Werkzeug nutzt ausschließlich die öffentliche Kunden-API mit deinem persönlichen Schlüssel. „EKD“ und „Ampere.IQ“ sind Bezeichnungen ihrer Inhaber. Siehe [Hintergrund & rechtliche Hinweise](../../README.md#hintergrund--rechtliche-hinweise).

- Nutzt nur die offizielle, lesende Kunden-API der EKD-Cloud.
- Läuft mit Python 3.9 oder neuer und braucht keine Zusatzpakete.
- Jede Antwort wird unverändert als JSON gespeichert, eine Datei pro Endpunkt und Tag. Nach einem Abbruch startest du das Skript einfach erneut, es macht dort weiter.
- Es schickt höchstens eine Anfrage alle 65 Sekunden. Wer öfter abfragt, wird von der API gesperrt.

## 1. API-Schlüssel erstellen

In der App „Ampere.IQ“: **Mehr → Konfiguration API-Zugang** → Schlüssel erzeugen.

## 2. `.env` anlegen

```
CLOUD_API_KEY=dein-schluessel
# optional:
# CLOUD_INSTALLATION_UUID=…     # nur nötig, wenn zum Schlüssel mehrere Anlagen gehören
# CLOUD_MIN_INTERVAL=65         # Sekunden zwischen Anfragen
# CLOUD_EXPORT_DIR=data/cloud-export
```

Gib die `.env`-Datei niemals weiter und lade sie nirgends hoch.

## 3. Testen und exportieren

```bash
python3 cloud_export.py check
```

```bash
nohup caffeinate -i python3 cloud_export.py export >> cloud-export.log 2>&1 &
```

`caffeinate` hält einen Mac wach. Unter Linux lässt du es einfach weg.

Mit `--start YYYY-MM-DD` überspringst du die automatische Suche nach dem ersten Datum mit Daten.

## Was gesichert wird

Pro Tag wird Folgendes gesichert, die Verläufe in 15-Minuten-Auflösung:

| Ordner | Endpunkt |
|---|---|
| `work/` | `history/common/work`: Energieflüsse (PV, Haus, Netz, Batterie) |
| `stateOfCharge/` | `history/stateOfCharge`: Ladestand des Akkus |
| `gridDraw/` | `history/gridDraw/work`: Netzbezug |
| `power/` | `history/common/power`: Leistung |
| `consumptionWork/`, `consumptionPower/` | Verbrauch |
| `totalWork/` | `total/common/work`: Tagessummen |

**Dauer:** etwa 7 Anfragen pro Tag Anlagenlaufzeit, also rund 2 Tage Laufzeit pro Jahr Anlagenbetrieb.
