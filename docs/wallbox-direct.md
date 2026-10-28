# Wallbox without evcc

Deutsch: [wallbox-direct.de.md](wallbox-direct.de.md)

Most wallboxes are controlled by OpenAmpere through [evcc](https://evcc.io), see [evcc.md](evcc.md). A few wallboxes
OpenAmpere talks to itself, without evcc and without a cloud. So far this is the **portable 11 kW wallbox sold as
"dé"** (with Tuya Wi-Fi). The **FoxESS AC EV Charger** OpenAmpere shows and can set its work mode (e.g. to unlock it);
its charging modes are a simulation that writes nothing, and its owner can switch on real control as a test (see
below). You can set up several of them, also next to a wallbox
through evcc.

> Reading is confirmed on a real device, control (starting and stopping charging, setting the current) not yet. Set
> the wallbox to **Von Hand** (manual) first and check what is shown before you choose another charging mode.

## What you need

- The wallbox is set up in the **Tuya Smart** or **Smart Life** app and on the same Wi-Fi as OpenAmpere.
- Its **local key**: a key of 16 characters. With it the wallbox talks encrypted in the home network. You get it once
  through the Tuya developer platform (below). After that OpenAmpere needs no cloud.
- Best give the wallbox the same IP address every time in your router.

## Getting the local key

The Tuya developer platform changes its pages often, so the names may differ slightly.

1. Create a free account on [iot.tuya.com](https://iot.tuya.com).
2. Under **Cloud → Development**, create a project ("Create Cloud Project"). As data center choose the one your app
   account is in, in Germany usually **Central Europe**.
3. Add the service **IoT Core** to the project. Without it the platform returns no device data. The service is a
   free trial that expires; it is enough for fetching the key once.
4. In the project, under **Devices → Link App Account**, show the QR code and scan it in the Tuya or Smart Life app
   (under **Me**, the scan icon at the top). The wallbox then appears in the project's device list, with its
   **device ID**.
5. Under **Cloud → API Explorer**, run the query of the device details ("Query Device Details") with the wallbox's
   device ID. The answer has the key under `local_key`.

If you know [tinytuya](https://github.com/jasonacox/tinytuya): its wizard (`python -m tinytuya wizard`) fetches the
key with the project's credentials as well.

**Important:** if you pair the wallbox again in the app (for example after a reset), the local key changes. Fetch the
new key the same way and enter it in OpenAmpere. The key opens the wallbox in the home network: do not pass it on or
post it anywhere.

**After restoring a backup on another computer**, OpenAmpere cannot read the stored local key: the backup holds it
only encrypted with the key of the old installation (`secret.key`). The wallbox keeps its settings and shows **Local
Key neu eintragen** (Enter the local key again). Until you enter the key again, OpenAmpere neither reads nor controls
it.

## Setting it up in OpenAmpere

1. Open **Mehr → Verbindung → Wallbox** (More → Connection → Wallbox) and choose **direkt** (directly).
2. Tap **Wallbox hinzufügen oder prüfen** (Add or check a wallbox) and under **Welche Wallbox hast du?** (Which
   wallbox do you have?) choose the dé wallbox (under "Anzeigen und steuern", show and control). The form then shows
   exactly the fields this wallbox needs and the steps to the local key.
3. Tap **Tuya-Wallbox im Heimnetz suchen** (Search the home network for a Tuya wallbox). If the wallbox is plugged in
   and on the Wi-Fi, it shows up; **Übernehmen** (Use) fills in its IP address and device ID. Otherwise copy the IP
   address from your router.
4. Enter the **local key** and the **highest charging current** OpenAmpere may set, for example 16 A for a red 16 A
   socket.
5. Tap **Verbindung prüfen** (Check connection). If it works, OpenAmpere shows the phases and the current that is set.
6. **Speichern** (Save).

Under **Geräte** (Devices) the wallbox then shows its state, charging power and current per phase.

## Charging modes

| Mode | What happens |
|---|---|
| **Von Hand** (manual) | OpenAmpere only shows the wallbox and changes nothing. You control it in its app as usual. Every new wallbox starts like this. |
| **Aus** (off) | Does not charge. A running charge is stopped. |
| **Solar** | Charges only with solar surplus. If it is no longer enough (a cloud), the wallbox goes on with the lowest current and the battery or the grid supply the rest. Only if it stays like this, charging pauses: after 10 minutes, adjustable under **Mehr → Wallbox** for the wallbox (**Solar-Laden pausieren nach**). |
| **Min + Solar** | Always charges with at least the lowest current, faster with sun. |
| **Sofort** (fast) | Charges right away, also from the battery or the grid. |

For **Solar**, **Min + Solar** and **Sofort** you choose the highest charging current on the wallbox's card under
**Geräte**, for each mode, for example 8 A for **Min + Solar** and 16 A for **Sofort**. On three phases 6 A are about
4.1 kW, 8 A about 5.5 kW and 16 A about 11 kW. It never goes above the highest charging current in the wallbox's
settings.

Without a car plugged in, OpenAmpere changes nothing on the wallbox; you can choose the charging mode beforehand,
it applies when you plug in. Many wallboxes start charging by themselves when a car is plugged in. If in **Solar**
mode there is not enough solar power then, OpenAmpere stops charging at the next check (after a few seconds).

OpenAmpere sets the current only in the steps the wallbox offers (for example 6, 8, 10, 13 and 16 A), and never above
your limit and the value from its app. Who gets solar power first (battery, wallbox, immersion heater) is set under
**Geräte**.

Something is changed only when **Aktiv** (active) is chosen at the top of **Geräte**. With **Testen** (test) OpenAmpere
only writes into the log what it would do.

## When the wallbox is unplugged

A portable wallbox is often not there. OpenAmpere then shows it as **nicht erreichbar seit …** (not reachable since
…), without old values, and asks again less and less often (up to every 5 minutes). As soon as it is back on the
Wi-Fi, OpenAmpere reconnects by itself and controls it in the chosen mode again. If you entered the device ID (the
search does that), OpenAmpere also finds the wallbox when the router gave it a new address.

## A message on your phone when charging

Under **Mehr → Benachrichtigungen** (More → Notifications), **Laden der Wallbox** (Wallbox charging) sends a message
when the car starts charging and when charging ends. The end message gives the energy since plugging in and the
duration of the charge. A wallbox that reports no energy (like the FoxESS AC EV Charger) gets only the duration. This
works for every wallbox OpenAmpere reads, also one it only shows. A pause of less than 15
minutes, for example while charging with surplus, does not count as the end. If the wallbox reports the car as full,
the end message comes at once, otherwise without unplugging only after 15 minutes. If OpenAmpere has just stopped
charging itself (charging with surplus), "full" waits 15 minutes as well: some wallboxes report "full" then too. If the car charges on within 12 hours and the wallbox had counted at least
0.5 kWh by then, the message is "Das Auto lädt wieder" (the car is charging again), otherwise "Das Laden hat
begonnen" (charging has started). If it charges on without having been unplugged, the next end message gives all the
energy since plugging in, without a duration.

## When OpenAmpere lets go

The wallbox has no safeguard of its own that steps in when OpenAmpere stops answering. So before its first change,
OpenAmpere remembers your own charging current and sets it back when it lets go of the wallbox:

- **Von Hand** chosen: your current comes back, a running charge goes on.
- **Control off** (at the top of Geräte), wallbox removed or switched off: your current comes back, and a charge
  OpenAmpere started is stopped.
- OpenAmpere **restarts** (for example for an update): the wallbox keeps charging as before.

Every change is in the log under **Mehr → Steuerung und Protokoll** (More → Control and log).

## In the Home Assistant app

The wallbox works the same in the app. Only the **search in the home network** does not, because the app has a
network of its own. Enter the IP address from your router by hand.

## Showing the FoxESS AC EV Charger (read-only)

The FoxESS AC EV Charger is also the wallbox EKD sold with its Ampere.IQ systems; in the router it shows up as
`EVC-…`. OpenAmpere shows it (#439): whether a car is plugged in, the charging power and the current per phase. Its
power counts in the house load like any wallbox. OpenAmpere does not control its charging yet (#318): the wallbox goes
on charging as it is set in its own app. The one thing OpenAmpere writes, only when you ask for it, is the wallbox's
work mode (see below).

1. Under **direkt → Wallbox hinzufügen oder prüfen** (Add or check a wallbox), choose the FoxESS AC EV Charger at
   **Welche Wallbox hast du?** (under "Nur anzeigen", show only) and keep **Anzeigen** (show).
2. Enter its IP address, or use **Wallbox im Heimnetz suchen** (search the home network). Port (usually 502) and unit
   ID (usually 1) are under **Erweitert** (advanced).
3. **Verbindung prüfen** (check the connection), then **Speichern** (save).

OpenAmpere asks the wallbox every few seconds, each time over a short connection it closes right afterwards.

- **On EKD systems** the previous smartbox holds the connection to the wallbox as long as it has power. OpenAmpere
  then cannot read the wallbox and shows it as not reachable. For OpenAmpere to show the wallbox, the smartbox has to
  stay disconnected from power.
- **Never evcc and OpenAmpere on the same wallbox:** it may allow only one connection. If evcc already reads it, do
  not add it here as well.
- OpenAmpere does not even read the energy counter, the card number or the serial number for this.

### Trying out the charging modes as a simulation

On the devices tab the FoxESS AC EV Charger offers the charging modes **Aus** (off), **Solar**, **Min + Solar** and
**Sofort** (fast) as well (#440). For this wallbox they are **only a simulation**: OpenAmpere works out what it would
do in that mode, as for a wallbox it controls, but writes nothing to the wallbox, whatever the master switch and the
test mode say. The wallbox goes on charging as it is set in its own app, for example as soon as you plug in a car.

- The card shows next to the real values what OpenAmpere would do now, for example "OpenAmpere würde jetzt mit 10 A
  auf 1 Phase laden" (would charge with 10 A on 1 phase now) or "würde jetzt nicht laden: Es gibt zu wenig
  Solarüberschuss" (would not charge: too little solar surplus).
- Every start and stop it would have made is in the control log (**Mehr → Steuerung**), marked "Simulation – nichts
  an die Wallbox geschrieben" (nothing written to the wallbox). Changes of the current are collected and written at
  most every 5 minutes as one entry: the current at that time and the range since the entry before, for example
  "zwischen 8 und 13 A" (between 8 and 13 A). So clouds do not bury real changes in the log. Nothing is logged while
  nothing changes. The card always shows the latest plan.
- The simulation uses the wallbox's own limits (lowest and highest current) and its automatic 1/3-phase switching:
  one phase below the lowest power on three phases (6 A, about 4.1 kW), three phases above that, switched at most as
  often as the wallbox allows. Your highest current and the waiting time before solar charging pauses
  (**Höchster Ladestrom**, **Solar-Laden pausieren nach**) count as for the dé wallbox.
- A charge the wallbox starts by itself counts as the wallbox's own: in **Solar** without enough surplus the
  simulation would stop it at once. Going back to **Von Hand**, or removing the wallbox, logs the stop that real
  control would send for a charge the simulation started.
- The simulated charging is not counted anywhere: your heating rod and other devices get the real surplus, as
  measured. The wallbox's real charging stays in the house load.
- The simulation runs as long as OpenAmpere runs; after a restart it starts again from what the wallbox does.
- Home Assistant only shows the wallbox (no charging mode), unless real control is switched on (see below).

### Real control (test)

Below the charging modes the card offers **Echte Steuerung einschalten (Test)** (switch on real control, #465). After
a question OpenAmpere then really controls this wallbox in the modes Aus, Solar, Min + Solar and Sofort: it starts and
stops charging and sets the charging current. This is new and not yet tried on a wallbox with OpenAmpere.

- **If OpenAmpere fails** (the computer crashes, the network is gone), the wallbox charges on after about a minute
  with 6 A until the car is full, from the grid if needed. OpenAmpere sets this fallback current in the wallbox once.
  The wallbox has no way to stop by itself when its controller is gone. When OpenAmpere is shut down as planned (an
  update, a restart) it stops a charge it started itself.
- Needed: the wallbox's **Betriebsmodus** (work mode) is **Gesteuert** (controlled, see below), the previous EKD
  smartbox is cut from power, and **Änderungen an Geräten** (changes to devices) is **Aktiv** (active). In test mode
  the control log only shows what OpenAmpere would do.
- While the car charges OpenAmpere writes the current again every 25 seconds; these writes are not in the control log.
  After a stop OpenAmpere writes nothing more to the wallbox until it starts it again (the wallbox would otherwise
  start by itself).
- OpenAmpere does not switch between one and three phases yet: solar charging needs enough surplus for 6 A on all
  phases of the wallbox.
- **Zurück zur Simulation** (back to the simulation) lets go of the wallbox: a charge OpenAmpere started is stopped.

### Work mode of the wallbox

The card also shows the wallbox's own **Betriebsmodus** (work mode, #464): **Gesteuert** (charges when an app, a card
or a controller starts it), **Plug & Charge** (charges when a car is plugged in) or **Gesperrt** (locked, does not
charge). A wallbox that went "locked" can be released here. OpenAmpere writes the setting once, after a question, and
reads it back. It needs **Aktiv** (active) under **Änderungen an Geräten** (changes to devices), the previous smartbox
cut from power, and a wallbox that answers right now. In test mode the control log only shows the change.

## Checking other wallboxes (read-only)

For more wallboxes OpenAmpere is preparing the direct way. Under **direkt → Wallbox hinzufügen oder prüfen**, choose
the wallbox at **Welche Wallbox hast du?**; for the FoxESS AC EV Charger then choose **Nur prüfen (Bericht)** (check
only, report). Then OpenAmpere checks the wallbox read-only, in the mode it is in, and shows what it answers. Nothing
is changed on the wallbox. The result goes into the diagnostics report (**Mehr → Diagnose**); sharing it helps to
confirm the registers on real devices and to control the wallbox soon. If the check could read the FoxESS AC EV
Charger, **Diese Wallbox in OpenAmpere anzeigen** (show this wallbox in OpenAmpere) takes over address, port and unit
ID. For other wallboxes, **Mehr → Diagnose → Gerät auslesen** (Read a device) reads any registers.

Optionally the check can also listen over **OCPP 1.6J** (#328): start the listener there and enter the address shown
as OCPP server in the manufacturer's app. OpenAmpere answers the wallbox with "Pending" and only asks for its settings;
it never starts or stops charging. While the wallbox is set to OCPP, neither the manufacturer's app nor evcc controls
it, and without a charging profile it charges without a limit: switch it back afterwards. In the Home Assistant app
the wallbox needs the direct address (direct access by IP must be on).

Technical details of the dé wallbox (data points, what is confirmed):
[devices/tuya/de-portable.md](devices/tuya/de-portable.md).
