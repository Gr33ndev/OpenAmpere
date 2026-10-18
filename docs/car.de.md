# Ladestand des Autos vom Hersteller

English: [car.md](car.md)

Eine Wallbox weiß nicht, wie voll das Auto ist. Das weiß nur das Auto, und sein Hersteller. OpenAmpere kann den
**Ladestand**, die **Reichweite** und den **Kilometerstand** beim Hersteller abrufen. Bisher geht das für Autos von
**Volkswagen, Audi, Škoda, SEAT und CUPRA** über das EU-Data-Act-Portal des Volkswagen-Konzerns.

Steckt das Auto an einer Wallbox, die OpenAmpere selbst steuert ([wallbox-direct.de.md](wallbox-direct.de.md)),
zeigt die Wallbox seinen Ladestand. Jeder Ladevorgang wird gespeichert, mit dem Ladestand beim Einstecken, der
geladenen Energie und den Kilometern dazwischen. Unter **Auswertung → Auto** rechnet OpenAmpere daraus den Verbrauch
pro 100 km aus.

Hast du die Wallbox über evcc und das Auto dort als Fahrzeug eingerichtet, brauchst du das hier nicht: evcc liefert
Ladestand und Reichweite schon ([evcc.de.md](evcc.de.md)).

> Die Werte sind nie live: Das Portal liefert etwa alle 15 Minuten, und die Werte sind dann mindestens 15 Minuten
> alt. OpenAmpere zeigt bei jedem Wert, wie alt er ist. OpenAmpere liest nur. Es schickt nichts an das Auto, und die
> Wallbox-Steuerung richtet sich nicht nach diesen Werten.

## Was du brauchst

- Ein Konto bei der Marke (dasselbe wie in der App der Marke, z. B. „My CUPRA“) mit deinem Auto.
- Einmal im Browser das Portal eingerichtet (unten).

## Das Portal einrichten (einmalig im Browser)

1. Öffne [eu-data-act.drivesomethinggreater.com](https://eu-data-act.drivesomethinggreater.com) und melde dich mit
   dem Konto deiner Marke an.
2. Bestätige die Einwilligung zur Datenübermittlung.
3. Verknüpfe dein Auto mit dem Konto, falls es dort noch nicht steht.
4. Starte eine **fortlaufende Datenanfrage** mit den Daten **alle 15 Minuten**. Ohne sie liefert das Portal nichts.

Bis die ersten Daten im Portal ankommen, können einige Stunden vergehen. OpenAmpere fragt so lange von selbst
immer wieder nach.

## In OpenAmpere einrichten

1. **Mehr → Verbindung → Auto** öffnen und **Auto hinzufügen** tippen.
2. Marke, E-Mail-Adresse und Passwort des Kontos eintragen.
3. **Autos aus dem Konto laden** tippen und dein Auto auswählen.
4. Unter **Lädt zu Hause an** die Wallbox wählen, die OpenAmpere selbst steuert (wenn du eine hast).
5. **Speichern**. Das Auto erscheint unter **Geräte**.

Dein Passwort speichert OpenAmpere verschlüsselt, weil das Portal keine dauerhafte Anmeldung kennt. Eine Sicherung
enthält es nur verschlüsselt, ohne den Schlüssel dazu, und die App zeigt es nie an. Lehnt das Portal die Anmeldung ab, versucht OpenAmpere es nicht
von selbst noch einmal (dein Konto könnte sonst gesperrt werden). Ändere dann E-Mail oder Passwort oder tippe
**Jetzt abrufen**.

## Gut zu wissen

- **Wie OpenAmpere die Ladevorgänge zählt:** Jedes Einstecken an der eingetragenen Wallbox ist ein Ladevorgang
  dieses Autos. Lädt dort einmal ein anderes Auto, zählt es mit.
- **Ladestand beim Einstecken:** Das Portal liefert spät. OpenAmpere nimmt deshalb den letzten Wert vor dem
  Einstecken (ein geparktes Auto behält seinen Ladestand), sonst den ersten danach.
- **Verbrauch:** Lädst du auch unterwegs, fehlt diese Energie, und der Verbrauch wirkt zu niedrig.
- **Was gespeichert bleibt:** Die Werte vom Hersteller und die Ladevorgänge behält OpenAmpere, bis du das Auto
  entfernst. Die Auswertung braucht sie für den Ladestand beim Einstecken und die gefahrenen Kilometer.
- **Das Portal ist keine offizielle Schnittstelle für Apps.** Ändert der Hersteller die Anmeldung, kann der Abruf
  ausfallen, bis OpenAmpere angepasst ist. Dann bitte ein Issue öffnen, mit dem Bericht aus **Mehr → Diagnose**
  (er enthält keine Anmeldung und keine Fahrgestellnummer).
- OpenAmpere gehört nicht zum Volkswagen-Konzern. Was über das Portal bekannt ist:
  [devices/cars/vw-eu-data-act.md](devices/cars/vw-eu-data-act.md) (englisch).
