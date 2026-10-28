# Wallbox ohne evcc

English: [wallbox-direct.md](wallbox-direct.md)

Die meisten Wallboxen steuert OpenAmpere über [evcc](https://evcc.io), siehe [evcc.de.md](evcc.de.md). Einige
Wallboxen spricht OpenAmpere selbst an, ohne evcc und ohne Cloud. Bisher ist das die **mobile 11-kW-Wallbox von
„dé“** (mit Tuya-WLAN). Den **FoxESS AC EV Charger** zeigt OpenAmpere an und kann seinen Betriebsmodus einstellen (z. B.
zum Entsperren); seine Lademodi gibt es als Simulation, die nichts schreibt, und als Test lässt sich die echte
Steuerung einschalten (siehe unten). Du kannst mehrere solche Wallboxen einrichten,
auch zusätzlich zu einer Wallbox über evcc.

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
| **Solar** | Lädt nur mit Solarüberschuss. Reicht er nicht mehr (eine Wolke), lädt die Wallbox mit dem kleinsten Strom weiter, den Rest liefern Speicher oder Netz. Erst wenn es so bleibt, pausiert das Laden: nach 10 Minuten, einstellbar unter **Mehr → Wallbox** bei der Wallbox (**Solar-Laden pausieren nach**). |
| **Min + Solar** | Lädt immer mindestens mit dem kleinsten Strom, bei Sonne schneller. |
| **Sofort** | Lädt sofort, auch aus dem Speicher oder dem Netz. |

Für **Solar**, **Min + Solar** und **Sofort** wählst du auf der Karte der Wallbox unter **Geräte** den höchsten
Ladestrom, jeweils für den Modus, zum Beispiel 8 A bei **Min + Solar** und 16 A bei **Sofort**. Auf drei Phasen sind
6 A etwa 4,1 kW, 8 A etwa 5,5 kW und 16 A etwa 11 kW. Mehr als der höchste Ladestrom aus den Einstellungen der
Wallbox geht nie.

Ohne angeschlossenes Auto ändert OpenAmpere nichts an der Wallbox; den Lademodus kannst du vorher wählen, er gilt
beim Einstecken. Viele Wallboxen starten beim Einstecken von selbst. Reicht im Modus **Solar** der Sonnenstrom
dann nicht, stoppt OpenAmpere das Laden gleich bei der nächsten Prüfung (nach wenigen Sekunden).

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

## Nachricht aufs Handy beim Laden

Unter **Mehr → Benachrichtigungen** schickt dir **Laden der Wallbox** eine Nachricht, wenn das Auto zu laden beginnt
und wenn das Laden endet. Die Meldung zum Ende nennt die Energie seit dem Anstecken und die Dauer der Ladung. Eine
Wallbox, die keine Energie meldet (wie der FoxESS AC EV Charger), bekommt nur die Dauer. Das gilt für jede Wallbox, die
OpenAmpere ausliest, auch wenn es sie nur anzeigt. Eine Pause von weniger als 15 Minuten, etwa
beim Laden mit Überschuss, gilt nicht als Ende. Meldet die Wallbox das Auto als voll, kommt die Meldung zum Ende
sofort, sonst ohne Abstecken erst nach 15 Minuten. Hat OpenAmpere das Laden gerade selbst gestoppt (Laden mit
Überschuss), wartet auch „voll“ 15 Minuten: Manche Wallboxen melden dann ebenfalls „voll“. Lädt das Auto danach innerhalb von 12 Stunden weiter und hatte die Wallbox bis dahin mindestens 0,5 kWh
gezählt, kommt „Das Auto lädt wieder“, sonst „Das Laden hat begonnen“. Lädt es weiter, ohne
dass es abgesteckt war, nennt die nächste Meldung zum Ende die ganze Energie seit dem Anstecken, ohne Dauer.

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

## FoxESS AC EV Charger anzeigen (nur lesen)

Der FoxESS AC EV Charger ist auch die Wallbox, die EKD mit seinen Ampere.IQ-Anlagen verkauft hat; im Router heißt sie
`EVC-…`. OpenAmpere zeigt ihn an (#439): ob ein Auto angeschlossen ist, mit welcher Leistung es lädt und den Strom je
Phase. Seine Leistung zählt im Hausverbrauch mit wie bei jeder Wallbox. Das Laden steuert OpenAmpere noch nicht
(#318): Die Wallbox lädt weiter so, wie sie in ihrer App eingestellt ist. Das Einzige, was OpenAmpere schreibt, und
nur auf deinen Wunsch, ist der Betriebsmodus der Wallbox (siehe unten).

1. Unter **direkt → Wallbox hinzufügen oder prüfen** bei **Welche Wallbox hast du?** den FoxESS AC EV Charger
   wählen (unter „Nur anzeigen“) und **Anzeigen** lassen.
2. Die IP-Adresse eintragen, oder **Wallbox im Heimnetz suchen**. Port (meist 502) und Geräteadresse (meist 1)
   stehen unter **Erweitert**.
3. **Verbindung prüfen**, dann **Speichern**.

OpenAmpere fragt die Wallbox alle paar Sekunden ab, jedes Mal über eine kurze Verbindung, die es danach gleich wieder
schließt.

- **Bei Anlagen von EKD** hält die bisherige Smartbox die Verbindung zur Wallbox, solange sie Strom hat. Dann kann
  OpenAmpere die Wallbox nicht lesen und zeigt „nicht erreichbar“. Soll OpenAmpere die Wallbox anzeigen, muss die
  Smartbox vom Strom getrennt bleiben.
- **Nie evcc und OpenAmpere an derselben Wallbox:** Vielleicht erlaubt sie nur eine Verbindung. Liest evcc sie schon,
  richte sie hier nicht zusätzlich ein.
- Zählerstand, Kartennummer und Seriennummer liest OpenAmpere dafür gar nicht erst.

### Die Lademodi als Simulation ausprobieren

Auf dem Reiter Geräte bietet der FoxESS AC EV Charger auch die Lademodi **Aus**, **Solar**, **Min + Solar** und
**Sofort** an (#440). Bei dieser Wallbox sind sie **nur eine Simulation**: OpenAmpere rechnet aus, was es in diesem
Modus tun würde, genau wie bei einer Wallbox, die es steuert. Es schreibt aber nichts an die Wallbox, egal wie
Hauptschalter und Testmodus stehen. Die Wallbox lädt weiter so, wie sie in ihrer App eingestellt ist, zum Beispiel
sobald du ein Auto einsteckst.

- Die Karte zeigt neben den echten Werten, was OpenAmpere jetzt tun würde, zum Beispiel „OpenAmpere würde jetzt mit
  10 A auf 1 Phase laden“ oder „OpenAmpere würde jetzt nicht laden: Es gibt zu wenig Solarüberschuss.“
- Jeder Start und jeder Stopp, den OpenAmpere vorgenommen hätte, steht im Protokoll (**Mehr → Steuerung**), markiert
  mit „Simulation – nichts an die Wallbox geschrieben“. Änderungen des Ladestroms sammelt OpenAmpere und schreibt sie
  höchstens alle 5 Minuten als einen Eintrag: den Ladestrom zu diesem Zeitpunkt und den Bereich seit dem Eintrag
  davor, zum Beispiel „zwischen 8 und 13 A“. So verdrängen Wolken keine echten Änderungen aus dem Protokoll. Solange
  sich nichts ändert, steht nichts darin. Die Karte zeigt immer den neuesten Plan.
- Die Simulation rechnet mit den Grenzen der Wallbox (kleinster und höchster Ladestrom) und ihrer automatischen
  Umschaltung zwischen 1 und 3 Phasen: eine Phase unterhalb der kleinsten Leistung auf drei Phasen (6 A, etwa
  4,1 kW), darüber drei Phasen, und umgeschaltet höchstens so oft, wie die Wallbox es erlaubt. Dein höchster
  Ladestrom und die Wartezeit vor dem Pausieren (**Höchster Ladestrom**, **Solar-Laden pausieren nach**) gelten wie
  bei der Wallbox von dé.
- Startet die Wallbox ein Laden von selbst, zählt das als ihr eigenes: Im Modus **Solar** ohne genug Überschuss würde
  die Simulation es gleich stoppen. Stellst du zurück auf **Von Hand** oder entfernst du die Wallbox, steht der Stopp
  im Protokoll, den eine echte Steuerung für ein Laden senden würde, das die Simulation gestartet hat.
- Das simulierte Laden zählt nirgends mit: Heizstab und andere Geräte bekommen den echten Überschuss, so wie er
  gemessen wird. Das echte Laden der Wallbox bleibt im Hausverbrauch.
- Die Simulation läuft, solange OpenAmpere läuft. Nach einem Neustart beginnt sie wieder bei dem, was die Wallbox
  gerade tut.
- Home Assistant zeigt die Wallbox nur an (kein Lademodus), außer die echte Steuerung ist eingeschaltet (siehe unten).

### Echte Steuerung (Test)

Unter den Lademodi bietet die Karte **Echte Steuerung einschalten (Test)** an (#465). Nach einer Rückfrage steuert
OpenAmpere diese Wallbox dann wirklich in den Lademodi Aus, Solar, Min + Solar und Sofort: Es startet und stoppt das
Laden und stellt den Ladestrom ein. Das ist neu und noch an keiner Wallbox mit OpenAmpere erprobt.

- **Fällt OpenAmpere aus** (der Rechner stürzt ab, das Netzwerk ist weg), lädt die Wallbox nach etwa einer Minute mit
  6 A weiter, bis das Auto voll ist, wenn nötig mit Strom aus dem Netz. OpenAmpere stellt diesen Ersatzstrom einmal in
  der Wallbox ein. Von selbst stoppen kann die Wallbox nicht, wenn ihre Steuerung fehlt. Beim geplanten Beenden (ein
  Update, ein Neustart) stoppt OpenAmpere ein Laden, das es selbst gestartet hat.
- Voraussetzung: Der **Betriebsmodus** der Wallbox ist **Gesteuert** (siehe unten), die bisherige Smartbox von EKD ist
  vom Strom getrennt, und **Änderungen an Geräten** steht auf **Aktiv**. Im Testmodus steht im Steuerungsprotokoll nur,
  was OpenAmpere tun würde. Schaltest du den Testmodus ein, während ein Laden läuft, das OpenAmpere gestartet hat,
  stoppt OpenAmpere es und gibt die Wallbox ab, weil sonst niemand mehr ihre Überwachung am Leben hält.
- Solange das Auto lädt, schreibt OpenAmpere den Ladestrom alle 25 Sekunden neu; das steht nicht im Protokoll. Nach
  einem Stopp schreibt OpenAmpere nichts mehr an die Wallbox, bis es sie wieder startet (sonst würde sie von selbst
  wieder laden).
- Zwischen einer und drei Phasen schaltet OpenAmpere noch nicht um: Solar-Laden braucht genug Überschuss für 6 A auf
  allen Phasen der Wallbox.
- Bei echter Steuerung bleibt der Betriebsmodus **Gesteuert**: OpenAmpere ändert ihn nicht, und wird er während eines
  Ladens, das OpenAmpere gestartet hat, in der App von FoxESS geändert, stoppt OpenAmpere das Laden und gibt die
  Wallbox ab.
- Übernimmt die Wallbox den Ersatzstrom von 6 A nicht, steht das auf der Karte, und OpenAmpere steuert sie 15 Minuten
  lang nicht; ein Laden, das es gestartet hat, wird gestoppt.
- **Von Hand** bei echter Steuerung: OpenAmpere gibt die Wallbox ab und stoppt ein Laden, das es selbst gestartet
  hat, weil die Wallbox sonst unbeaufsichtigt mit 6 A weiterladen würde. Starte das Laden dann in der App von FoxESS
  oder mit einer Ladekarte.
- **Zurück zur Simulation** gibt die Wallbox ab: Ein Laden, das OpenAmpere gestartet hat, wird gestoppt.

### Betriebsmodus der Wallbox

Die Karte zeigt auch den **Betriebsmodus** der Wallbox selbst (#464): **Gesteuert** (lädt, wenn eine App, eine Karte
oder eine Steuerung den Ladevorgang startet), **Plug & Charge** (lädt beim Einstecken) oder **Gesperrt** (lädt nicht).
Eine Wallbox, die sich gesperrt hat, lässt sich hier wieder freigeben. OpenAmpere schreibt die Einstellung nach einer
Rückfrage einmal und liest sie zurück. Dafür muss **Änderungen an Geräten** auf **Aktiv** stehen, die bisherige
Smartbox vom Strom getrennt sein und die Wallbox gerade antworten. Im Testmodus steht die Änderung nur im
Steuerungsprotokoll.

## Andere Wallboxen prüfen (nur lesen)

Für weitere Wallboxen bereitet OpenAmpere den direkten Weg vor. Unter **direkt → Wallbox hinzufügen oder prüfen**
wählst du sie bei **Welche Wallbox hast du?**; beim FoxESS AC EV Charger wählst du dann **Nur prüfen (Bericht)**.
Dann prüft OpenAmpere die Wallbox nur lesend, in der Steuerungsart, in der sie gerade steht, und zeigt, was sie
antwortet. An der Wallbox wird nichts geändert. Das Ergebnis kommt in den Diagnosebericht (**Mehr → Diagnose**); wenn
du ihn teilst, hilft das, die Register an echten Geräten zu bestätigen und die Wallbox bald auch zu steuern. Konnte
die Prüfung den FoxESS AC EV Charger lesen, übernimmt **Diese Wallbox in OpenAmpere anzeigen** Adresse, Port und
Geräteadresse. Für andere Wallboxen liest **Mehr → Diagnose → Gerät auslesen** beliebige Register.

Wahlweise kann die Prüfung auch per **OCPP 1.6J** zuhören (#328): Starte dort den Zuhörer und trage die angezeigte
Adresse in der App des Herstellers als OCPP-Server ein. OpenAmpere antwortet der Wallbox mit „Pending“ und fragt nur
ihre Einstellungen ab; es startet und stoppt kein Laden. Solange die Wallbox auf OCPP steht, steuern sie weder die App
des Herstellers noch evcc, und ohne Ladeprofil lädt sie ohne Begrenzung: Stell sie danach wieder zurück. In der
Home-Assistant-App braucht die Wallbox die direkte Adresse (der direkte Zugriff per IP muss an sein).

Technische Einzelheiten zur dé-Wallbox (Datenpunkte, was bestätigt ist):
[devices/tuya/de-portable.md](devices/tuya/de-portable.md).
