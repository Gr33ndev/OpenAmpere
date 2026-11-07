# Ladestand des Autos vom Hersteller

English: [car.md](car.md)

Eine Wallbox weiß nicht, wie voll das Auto ist. Das weiß nur das Auto, und sein Hersteller. OpenAmpere kann den
**Ladestand**, die **Reichweite** und den **Kilometerstand** beim Hersteller abrufen. Bisher geht das für Autos von
**Volkswagen, Audi, Škoda, SEAT und CUPRA** über das EU-Data-Act-Portal des Volkswagen-Konzerns und für **BMW und
MINI** über BMW CarData ([unten](#bmw-und-mini)).

Steckt das Auto an einer Wallbox, die OpenAmpere selbst steuert ([wallbox-direct.de.md](wallbox-direct.de.md)),
zeigt die Wallbox seinen Ladestand. Jeder Ladevorgang wird gespeichert, mit dem Ladestand beim Einstecken, der
geladenen Energie und den Kilometern dazwischen. Unter **Auswertung → Auto** rechnet OpenAmpere daraus den Verbrauch
pro 100 km aus.

Hast du die Wallbox über evcc und das Auto dort als Fahrzeug eingerichtet, brauchst du das hier nicht: evcc liefert
Ladestand und Reichweite schon ([evcc.de.md](evcc.de.md)).

> Die Werte sind nie live: Das Portal des Volkswagen-Konzerns liefert etwa alle 15 Minuten, und die Werte sind
> dann mindestens 15 Minuten alt; bei BMW und MINI fragt OpenAmpere etwa alle 40 Minuten. OpenAmpere zeigt bei jedem Wert, wie alt er ist. OpenAmpere liest nur. Es schickt nichts an das Auto, und die
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
enthält es nur verschlüsselt, ohne `secret.key` nicht lesbar (der Schlüssel steht nicht in den Sicherungen von OpenAmpere; eine Sicherung von Home Assistant enthält beides), und die App
zeigt es nie an. Lehnt das Portal die Anmeldung ab, versucht OpenAmpere es nicht
von selbst noch einmal (dein Konto könnte sonst gesperrt werden). Ändere dann E-Mail oder Passwort oder tippe
**Jetzt abrufen**.

## BMW und MINI

BMW und MINI bieten ihren Kundinnen und Kunden die Daten des Autos über **BMW CarData** an. Ein Passwort braucht
OpenAmpere dafür nicht: Du erstellst im Portal deiner Marke eine **Client-ID** und bestätigst die Anmeldung von
OpenAmpere einmal mit einem Code im Browser.

Was du brauchst: ein Konto bei BMW oder MINI (wie in der App „My BMW“ oder „MINI“), in dem du **Hauptnutzer** des
Autos bist. Nur der Hauptnutzer bekommt die Daten.

### Einmalig im Browser

1. Öffne das Portal deiner Marke und melde dich an:
   [bmw.de/de-de/mybmw/vehicle-overview](https://www.bmw.de/de-de/mybmw/vehicle-overview) oder
   [mini.de/de-de/mymini/vehicle-overview](https://www.mini.de/de-de/mymini/vehicle-overview). Wähle dein Auto.
2. Öffne **BMW CarData** bzw. **MINI CarData** und frage den **Zugang zur CarData API** an. Erst danach die
   Client-ID erstellen, sonst klappt die Anmeldung nicht.
3. Erstelle eine **Client-ID** und kopiere sie sofort. Das Portal zeigt sie nur dieses eine Mal.

### In OpenAmpere

1. **Mehr → Verbindung → Auto** öffnen und **Auto hinzufügen** tippen.
2. Als Marke **BMW** oder **MINI** wählen und die Client-ID eintragen.
3. **Anmeldung starten** tippen. OpenAmpere zeigt einen Link und einen Code. Öffne den Link, melde dich an und
   bestätige den Code. OpenAmpere merkt die Bestätigung nach ein paar Sekunden von selbst.
4. **Autos aus dem Konto laden** tippen, dein Auto auswählen, die Wallbox wählen und **Speichern**.

Gut zu wissen bei BMW und MINI:

- **Die Anmeldung gilt zwei Wochen** und verlängert sich bei jedem Abruf. War OpenAmpere länger aus (z. B. das
  Tablet), musst du sie unter **Ändern → Neu anmelden** wieder bestätigen. OpenAmpere speichert nur diesen
  Anmeldeschlüssel, verschlüsselt wie ein Passwort.
- **BMW erlaubt 50 Abfragen am Tag** für das ganze Konto. OpenAmpere fragt etwa alle 40 Minuten, bei zwei Autos im selben Konto jedes etwa alle 80 Minuten. Nutzt du BMW
  CarData auch in evcc oder Home Assistant, teilen sie sich diese Abfragen; ist das Tageslimit erreicht, wartet
  OpenAmpere ein paar Stunden.
- **Neue Werte gibt es nur, wenn das Auto welche schickt**, etwa beim Fahren und Laden. Ein geparktes Auto, das
  schläft, meldet nichts Neues.
- Klappt die Anmeldung nicht („gibt keinen Zugang heraus“): im Portal die Client-ID löschen, eine neue erstellen
  und diese in OpenAmpere eintragen.
- Was über BMW CarData bekannt ist: [devices/cars/bmw-cardata.md](devices/cars/bmw-cardata.md) (englisch).

## Gut zu wissen

- **Wie OpenAmpere die Ladevorgänge zählt:** Jedes Einstecken an der eingetragenen Wallbox ist ein Ladevorgang
  dieses Autos. Lädt dort einmal ein anderes Auto, zählt es mit.
- **Ladestand beim Einstecken:** Das Portal liefert spät. OpenAmpere nimmt deshalb den letzten Wert vor dem
  Einstecken (ein geparktes Auto behält seinen Ladestand), sonst den ersten danach.
- **Verbrauch:** Lädst du auch unterwegs, fehlt diese Energie, und der Verbrauch wirkt zu niedrig.
- **Was gespeichert bleibt:** Die einzelnen Werte vom Hersteller behält OpenAmpere 90 Tage. Jeder Ladevorgang
  behält seinen Ladestand beim Einstecken und Abstecken und den Kilometerstand, bis du das Auto entfernst; die
  Auswertung braucht sie für den Verbrauch.
- **Das Portal ist keine offizielle Schnittstelle für Apps.** Ändert der Hersteller die Anmeldung, kann der Abruf
  ausfallen, bis OpenAmpere angepasst ist. Dann bitte ein Issue öffnen, mit dem Bericht aus **Mehr → Diagnose**
  (er enthält keine Anmeldung und keine Fahrgestellnummer).
- OpenAmpere gehört weder zum Volkswagen-Konzern noch zur BMW Group. Was über das Portal bekannt ist:
  [devices/cars/vw-eu-data-act.md](devices/cars/vw-eu-data-act.md) (englisch).
