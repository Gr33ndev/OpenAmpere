# Wallbox without evcc

Deutsch: [wallbox-direct.de.md](wallbox-direct.de.md)

Most wallboxes are controlled by OpenAmpere through [evcc](https://evcc.io), see [evcc.md](evcc.md). A few wallboxes
OpenAmpere talks to itself, without evcc and without a cloud. So far this is the **portable 11 kW wallbox sold as
"dé"** (with Tuya Wi-Fi). You can set up several of them, also next to a wallbox through evcc.

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

## Checking other wallboxes (read-only)

For more wallboxes OpenAmpere is preparing the direct way, starting with the FoxESS AC EV Charger (#318). Under
**direkt → Wallbox hinzufügen oder prüfen** (Add or check a wallbox), choose it under **Welche Wallbox hast du?**
in "Nur prüfen (Vorschau)" (check only, preview). Then OpenAmpere checks the wallbox
read-only, in the mode it is in, and shows what it answers. Nothing is changed on the wallbox. The result goes into
the diagnostics report (**Mehr → Diagnose**); sharing it helps to confirm the registers on real devices. For other
wallboxes, **Mehr → Diagnose → Gerät auslesen** (Read a device) reads any registers.

Optionally the check can also listen over **OCPP 1.6J** (#328): start the listener there and enter the address shown
as OCPP server in the manufacturer's app. OpenAmpere answers the wallbox with "Pending" and only asks for its settings;
it never starts or stops charging. While the wallbox is set to OCPP, neither the manufacturer's app nor evcc controls
it, and without a charging profile it charges without a limit: switch it back afterwards. In the Home Assistant app
the wallbox needs the direct address (direct access by IP must be on).

Technical details of the dé wallbox (data points, what is confirmed):
[devices/tuya/de-portable.md](devices/tuya/de-portable.md).
