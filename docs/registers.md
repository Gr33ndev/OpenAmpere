# FoxESS H3: Modbus-Register

Gilt auch für baugleiche Geräte, die unter anderen Namen verkauft wurden.

Stand: 2026-10-02. Quellen (nur Fakten, kein Code übernommen):
- [foxess_modbus](https://github.com/nathanmarlor/foxess_modbus) (MIT): `entities/entity_descriptions.py`, `inverter_profiles.py`, `remote_control_manager.py`
- weitere Community-Dokumentation zu FoxESS-H3-basierten Speichern (nur Registeradressen als Fakten)

**Noch offen, muss am echten Gerät geprüft werden:**
1. Welche Registerkarte der E3 nutzt (Modellstring in 30000).
2. Ob Lesen über FC03 (holding) oder FC04 (input) erfolgt. foxess_modbus liest die neue Karte per FC03, andere Quellen per FC04. OpenAmpere probiert beides automatisch.

## Verbindung

- Modbus TCP, Port 502, Geräteadresse (Unit-ID) 247
- **Wenige TCP-Verbindungen erlaubt.** Ein bereits angeschlossener Energiemanager hält vermutlich schon eine. Deshalb nur eine dauerhafte Verbindung aufbauen und alle Anfragen nacheinander schicken.
- Nach dem Verbindungsaufbau 1 s warten, nach jeder Anfrage 30 ms. Höchstens 100 Register pro Leseanfrage. Abfragerate 5–10 s.
- 32-Bit-Werte: Das höherwertige Wort liegt an der niedrigeren Adresse.

## Modellerkennung

- 30000–30015: Modellstring als ASCII, 2 Zeichen pro Register, führende Leerzeichen entfernen. 30016 ff.: Seriennummer (neue Karte).
- Regex in foxess, in dieser Reihenfolge geprüft:
  - `^H3-([\d.]+)-(?:Smart|M)` → H3 Smart (neue Karte)
  - `^[HP]3-Pro-([\d.]+)` → H3 Pro (neue Karte)
  - `^H3-([\d.]+)` → klassischer H3 (alte Karte)
- Firmware-Versionen: alte Karte 30016–30018, neue Karte 36001–36003.

## Neue Karte (H3 Smart/Pro, wahrscheinlich E3)

| Wert | Register | Typ / Skalierung |
|---|---|---|
| PV1–4 Leistung | 39279/81/83/85 (je 2 Reg.) | I32, W |
| PV gesamt (E3) | 39118 | I32 |
| MPPT1 / MPPT2 | 39329 / 39333 | I32, W |
| PV-Spannung / -Strom | 39070/72/74/76 / 39071/73/75/77 | 0,1 V / 0,01 A |
| Netz gesamt | 38814 | I32, 0,1 W, **+ = Einspeisung** |
| Netz L1/L2/L3 | 38816–38821 | I32, 0,1 W |
| Zähler 2 (externe PV) | 38914 ff., aktiv wenn 38901 = 1 | I32, 0,1 W |
| Hausverbrauch | 39225 (L1–L3: 39219–39224) | I32, W |
| Batterieleistung | 39237 | I32, W, **+ = Entladen** |
| BMS1 verbunden | 37002 (BMS2: 37700) | Flag |
| Batterie U / I / T | 37609 / 37610 / 37611 | 0,1 |
| Ladestand / SoH | 37612 / 37624 | % |
| Zellspannung max/min | 37619 / 37620 | mV |
| Restkapazität | 37632 | |
| Status / Netztrennung / Alarme | 39063 / 39065 Bit 0 / 39067–39069 | |

Energiezähler, jeweils U32 mit 0,01 kWh, als Paar (gesamt, heute):

| PV | Laden | Entladen | Einspeisung | Netzbezug | Ertrag | Verbrauch |
|---|---|---|---|---|---|---|
| 39601 / 39603 | 39605 / 39607 | 39609 / 39611 | 39613 / 39615 | 39617 / 39619 | 39621 / 39623 | 39629 / 39631 |

### Schreiben (neue Karte)

| Einstellung | Register | Werte |
|---|---|---|
| Betriebsmodus | 49203 (holding, FC06) | 1 Eigenverbrauch, 2 Einspeisung bevorzugt, 3 Backup/Notstrom, 4 Spitzenlast |
| Max. Lade-/Entladestrom | 46607 / 46608 | |
| Min-SoC / Max-SoC / Min-SoC am Netz | 46609 / 46610 / 46611 | % |
| Fernsteuerung | 46001 an/aus, 46002 Timeout, 46003–46004 Wirkleistung (I32, W, + = Entladen/Einspeisen) | |
| Import-/Exportlimit | 46501–46502 / 46616–46617 | I32, W (laut foxess nur bei Smart) |

Die Einspeisebegrenzung (Exportlimit, 46616–46617) ist in OpenAmpere unter **Mehr → Einspeisebegrenzung** einstellbar. Am echten E3 ist sie noch nicht geprüft. Erhöhen geht nur mit Freigabe der Steuerung und mit der Erklärung, dass die schriftliche Zustimmung des Netzbetreibers vorliegt. Das Aktenzeichen dieser Zustimmung wird protokolliert. Senken ist jederzeit möglich. In der alten Registerkarte gibt es dafür kein bekanntes Register.

## Alte Karte (klassischer H3)

| Wert | Register | Skalierung |
|---|---|---|
| PV1 U/I/P, PV2 U/I/P | 31000–31005 | 0,1 V / 0,1 A / W |
| Netz-CT L1–L3 | 31026–31028 | W, + = Einspeisung |
| Last L1–L3 | 31029–31031 | W (kein Summenregister) |
| Batterie U / I / P / T / SoC | 31034 / 31035 / 31036 / 31037 / 31038 | P: + = Entladen |
| Status / Fehler | 31041 / 31044–31051 | |
| SoH (FW ≥ 1.80) | 31090 | % |

Energiezähler: U32 + Tageswert, 0,1 kWh

| | Gesamt | Heute |
|---|---|---|
| PV | 32000 | 32002 |
| Laden | 32003 | 32005 |
| Entladen | 32006 | 32008 |
| Einspeisung | 32009 | 32011 |
| Netzbezug | 32012 | 32014 |
| Last | 32021 | 32023 |

Schreiben:

| Einstellung | Register | Werte |
|---|---|---|
| Betriebsmodus | 41000 | 0 Eigenverbrauch, 1 Einspeisung, 2 Backup, 4 Spitzenlast |
| Lade-/Entladestrom | 41007 / 41008 | 0,1 A |
| Min-SoC / Max-SoC / Min-SoC am Netz | 41009 / 41010 / 41011 | % |
| Fernsteuerung | 44000 an/aus, 44001 Timeout, 44002–44003 Wirkleistung | I32, W |

Bei der alten Karte jedes Register im Bereich 41xxx einzeln lesen. 41001–41006, 41012–41013 und 41015 nicht lesen, sie sind ungültig.

## Regeln fürs Schreiben (aus foxess_modbus)

- Fernsteuerung: zuerst den Timeout schreiben, dann Enable, beides einzeln mit FC06. Die Wirkleistung mit FC16 schreiben, 2 Register ab dem höherwertigen Wort.
- **Vor dem Aktivieren einen Rückfall-Betriebsmodus setzen.** Läuft der Watchdog ab, verhält sich der Wechselrichter dann sinnvoll.
- Fernsteuerung nur abschalten, wenn wir sie selbst eingeschaltet haben. Andere Energiemanager und die FoxESS-Cloud nutzen dieselben Register.
- Der Wechselrichter beachtet Max-SoC beim Zwangsladen nicht. Die Software muss bei SoC ≥ Max-SoC selbst stoppen.
- Nach dem Schreiben den Wert zurücklesen. Das Zurücklesen kann ein paar Sekunden verzögert sein.
- Zeitgesteuerte Ladefenster gibt es beim H3 nicht als Register. Laden nach Strompreis läuft deshalb über die Fernsteuerung.


## SAJ H2 / HS2

Die Fakten stammen aus Community-Projekten; Quellen und Lizenzen stehen in [NOTICE](../NOTICE). Der Treiber liegt in `src/openampere/drivers/saj/`.

- **Verbindung:** FC03, Port 502, Geräteadresse **1 oder 2**. Welche, hängt von der Installation ab; die Erkennung probiert beide.
- **Erkennung:** Block 0x8F00 mit 29 Registern:
  - Gerätetyp, Nennleistung in W, Kommunikationsversion
  - Seriennummer in 0x8F03–0x8F0C
  - Produktcode und Firmware-Versionen
  
  Akzeptiert werden die bekannten Typcodes 0x0055–0x005D sowie Codes aus dem Familienbereich 0x0050–0x006F, diese mit Warnung im Log. Zusätzliche Plausibilitätsprüfung: Betriebsmodus 0x4004 ≤ 9 und Ladestand 0x406F ≤ 10000.
- **Messwerte:**

  | Wert | Register | Skalierung / Vorzeichen |
  |---|---|---|
  | Temperaturen | 0x4010 (Kühlkörper), 0x4011 (Umgebung) | ×0,1 °C |
  | Batterie U / I | 0x4069 / 0x406A | ×0,1 V / ×0,01 A |
  | Batterie Temperatur | 0x406E | ×0,1 °C |
  | Ladestand | 0x406F | ×0,01 % |
  | PV1–4 | 0x4071–0x407C | U ×0,1 V, I ×0,01 A, P in W |
  | Haus | 0x40A0 | W |
  | PV gesamt | 0x40A5 | W |
  | Batterieleistung | 0x40A6 | W, + = Entladen |
  | Netz | 0x40AD | W, + = Bezug |

- **Zähler:** U32 in 0,01 kWh ab 0x40BF. Für den Netzbezug und die Einspeisung aller drei Phasen gelten die Summenzähler ab 0x4167; sie fehlen bei mancher Firmware, dann werden die L1-Zähler genutzt. Achtung: SAJ nennt die Einspeisung „sell“ und den Netzbezug „feed-in“.
- **Noch am echten Gerät zu prüfen:**
  - die Vorzeichen von Batterie- und Netzleistung
  - ob der PV-Zähler alle MPPTs umfasst
- **Steuerung:** in OpenAmpere bewusst gesperrt. Bekannt, aber ungeprüft:
  - AppMode 0x3647
  - SoC-Grenzen 0x3644–0x3646
  - Exportlimit 0x365A in Promille der Nennleistung
