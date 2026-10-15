# Wallbox ohne evcc

English: [wallbox-direct.md](wallbox-direct.md)

Die meisten Wallboxen steuert OpenAmpere über [evcc](https://evcc.io), siehe [evcc.de.md](evcc.de.md). Einige
Wallboxen spricht OpenAmpere selbst an, ohne evcc und ohne Cloud. Bisher ist das die **mobile 11-kW-Wallbox von
„dé“** (mit Tuya-WLAN). Du kannst mehrere solche Wallboxen einrichten, auch zusätzlich zu einer Wallbox über evcc.

> Das Auslesen ist an einem echten Gerät bestätigt, das Steuern (Laden starten und stoppen, Ladestrom setzen) noch
> nicht. Stell die Wallbox zuerst auf **Von Hand** und prüfe die Anzeige, bevor du einen anderen Lademodus wählst.

## Was du brauchst

- Die Wallbox ist in der App **Tuya Smart** oder **Smart Life** eingerichtet und im selben WLAN wie OpenAmpere.
- Ihren **Local Key**: einen Schlüssel aus 16 Zeichen. Mit ihm spricht die Wallbox im Heimnetz verschlüsselt. Du
  bekommst ihn einmalig über die Tuya-Entwicklerplattform (unten). Danach braucht OpenAmpere keine Cloud.
- Am besten gibst du der Wallbox im Router immer dieselbe IP-Adresse.

## Den Local Key besorgen

Die Tuya-Entwicklerplattform ändert ihre Oberfläche öfter. Die Namen können deshalb leicht abweichen.

1. Auf [iot.tuya.com](https://iot.tuya.com) ein kostenloses Konto anlegen.
2. Unter **Cloud → Development** ein Projekt anlegen („Create Cloud Project“). Als Rechenzentrum (Data Center) das
   wählen, in dem auch dein App-Konto liegt, in Deutschland meist **Central Europe**.
3. Dem Projekt den Dienst **IoT Core** hinzufügen. Ohne ihn liefert die Plattform keine Gerätedaten. Der Dienst ist
   eine kostenlose Testversion, die abläuft; für den einmaligen Abruf reicht sie.
4. Im Projekt unter **Devices → Link App Account** den QR-Code anzeigen und in der Tuya- oder Smart-Life-App
   (unter **Ich**, oben das Scan-Symbol) einscannen. Danach erscheint die Wallbox in der Geräteliste des Projekts,
   mit ihrer **Geräte-ID**.
5. Unter **Cloud → API Explorer** die Abfrage der Gerätedetails („Query Device Details“) mit der Geräte-ID der
   Wallbox ausführen. In der Antwort steht der Schlüssel unter `local_key`.

Wer das Programm [tinytuya](https://github.com/jasonacox/tinytuya) kennt: sein Assistent (`python -m tinytuya wizard`)
holt den Schlüssel mit den Zugangsdaten des Projekts ebenso.

**Wichtig:** Verbindest du die Wallbox in der App neu (zum Beispiel nach einem Zurücksetzen), ändert sich der Local
Key. Dann den neuen Schlüssel auf dieselbe Weise abrufen und in OpenAmpere eintragen. Der Schlüssel öffnet die
Wallbox im Heimnetz: Gib ihn nicht weiter und poste ihn nirgends.

**Nach dem Wiederherstellen einer Sicherung auf einem anderen Rechner** kann OpenAmpere den gespeicherten Local Key
nicht lesen: In der Sicherung steht er nur verschlüsselt mit dem Schlüssel der alten Installation (`secret.key`). Die
Wallbox behält ihre Einstellungen und zeigt „Local Key neu eintragen“. Bis du den Schlüssel neu einträgst, liest und
steuert OpenAmpere sie nicht.

## In OpenAmpere einrichten

1. **Mehr → Verbindung → Wallbox** öffnen und **direkt** wählen.
2. **Wallbox hinzufügen oder prüfen** antippen und bei **Welche Wallbox hast du?** die dé-Wallbox wählen (unter
   „Anzeigen und steuern“). Das Formular zeigt dann genau die Felder, die diese Wallbox braucht, und die Schritte
   zum Local Key.
3. **Tuya-Wallbox im Heimnetz suchen** antippen. Ist die Wallbox eingesteckt und im WLAN, erscheint sie; mit
   **Übernehmen** werden IP-Adresse und Geräte-ID eingetragen. Sonst die IP-Adresse aus dem Router abschreiben.
4. Den **Local Key** eintragen und den **höchsten Ladestrom**, den OpenAmpere einstellen darf, zum Beispiel 16 A für
   eine rote 16-A-Steckdose.
5. **Verbindung prüfen** antippen. Bei Erfolg zeigt OpenAmpere die Phasen und den eingestellten Ladestrom.
6. **Speichern**.

Unter **Geräte** erscheint die Wallbox dann mit ihrem Zustand, der Ladeleistung und dem Strom je Phase.

## Lademodi

| Modus | Was passiert |
|---|---|
| **Von Hand** | OpenAmpere zeigt die Wallbox nur an und ändert nichts. Du steuerst sie wie gewohnt in ihrer App. So ist jede neue Wallbox eingestellt. |
| **Aus** | Lädt nicht. Ein laufendes Laden wird gestoppt. |
| **Solar** | Lädt nur mit Solarüberschuss. Reicht er nicht mehr, pausiert das Laden nach einigen Minuten. |
| **Min + Solar** | Lädt immer mit dem kleinsten Strom, bei Sonne schneller. |
| **Sofort** | Lädt sofort mit dem höchsten erlaubten Strom, auch aus dem Netz. |

OpenAmpere stellt den Ladestrom nur in den Stufen ein, die die Wallbox anbietet (zum Beispiel 6, 8, 10, 13 und 16 A),
und nie höher als dein Höchstwert und der Wert aus ihrer App. Wer zuerst Sonnenstrom bekommt (Speicher, Wallbox,
Heizstab), legst du unter **Geräte** fest.

Geändert wird nur, wenn unter **Geräte** oben **Aktiv** gewählt ist. Mit **Testen** schreibt OpenAmpere nur ins
Steuerprotokoll, was es tun würde.

## Wenn die Wallbox ausgesteckt ist

Eine mobile Wallbox ist oft nicht da. OpenAmpere zeigt sie dann als **nicht erreichbar seit …** an, ohne alte
Werte, und fragt in immer längeren Abständen nach (bis alle 5 Minuten). Sobald sie wieder im WLAN ist, verbindet sich
OpenAmpere von selbst und steuert sie im gewählten Modus weiter. Hast du die Geräte-ID eingetragen (die Suche macht
das), findet OpenAmpere die Wallbox auch, wenn der Router ihr eine neue Adresse gegeben hat.

## Wenn OpenAmpere loslässt

Die Wallbox hat keine eigene Sicherung, die eingreift, wenn OpenAmpere nicht mehr antwortet. Deshalb merkt sich
OpenAmpere vor der ersten Änderung deinen eigenen Ladestrom und stellt ihn wieder ein, wenn es die Wallbox abgibt:

- **Von Hand** gewählt: dein Ladestrom kommt zurück, ein laufendes Laden geht weiter.
- **Steuerung aus** (oben unter Geräte), Wallbox entfernt oder ausgeschaltet: dein Ladestrom kommt zurück, und ein
  Laden, das OpenAmpere gestartet hat, wird gestoppt.
- OpenAmpere wird **neu gestartet** (zum Beispiel bei einem Update): die Wallbox lädt so weiter wie zuvor.

Jede Änderung steht im Protokoll unter **Mehr → Steuerung und Protokoll**.

## In der Home-Assistant-App

Die Wallbox funktioniert in der App genauso. Nur die **Suche im Heimnetz** geht dort nicht, weil die App ein eigenes
Netz hat. Trag die IP-Adresse aus dem Router von Hand ein.

## Andere Wallboxen prüfen (nur lesen)

Für weitere Wallboxen bereitet OpenAmpere den direkten Weg vor, zuerst für den FoxESS AC EV Charger (#318). Unter
**direkt → Wallbox hinzufügen oder prüfen** wählst du sie bei **Welche Wallbox hast du?** unter „Nur prüfen
(Vorschau)“. Dann prüft OpenAmpere die Wallbox nur lesend, in der Steuerungsart, in der
sie gerade steht, und zeigt, was sie antwortet. An der Wallbox wird nichts geändert. Das Ergebnis kommt in den
Diagnosebericht (**Mehr → Diagnose**); wenn du ihn teilst, hilft das, die Register an echten Geräten zu bestätigen.
Für andere Wallboxen liest **Mehr → Diagnose → Gerät auslesen** beliebige Register.

Wahlweise kann die Prüfung auch per **OCPP 1.6J** zuhören (#328): Starte dort den Zuhörer und trage die angezeigte
Adresse in der App des Herstellers als OCPP-Server ein. OpenAmpere antwortet der Wallbox mit „Pending“ und fragt nur
ihre Einstellungen ab; es startet und stoppt kein Laden. Solange die Wallbox auf OCPP steht, steuern sie weder die App
des Herstellers noch evcc, und ohne Ladeprofil lädt sie ohne Begrenzung: Stell sie danach wieder zurück. In der
Home-Assistant-App braucht die Wallbox die direkte Adresse (der direkte Zugriff per IP muss an sein).

Technische Einzelheiten zur dé-Wallbox (Datenpunkte, was bestätigt ist):
[devices/tuya/de-portable.md](devices/tuya/de-portable.md).
