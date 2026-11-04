Deutsch: [android.de.md](android.de.md)

# OpenAmpere on an Android tablet

No Linux computer and no Home Assistant, but an old Android tablet? Then OpenAmpere can run on the tablet. The app
brings everything along: it reads your solar system around the clock, stores the readings on the tablet and shows the
same web app as the other ways. Phones and computers in the home network open it in the browser.

The Android app is new. If something does not work, please open an
[issue](https://github.com/Gr33ndev/OpenAmpere/issues).

> **OpenAmpere already runs with Docker or in Home Assistant?** Read [Moving](#moving) first. Only one OpenAmpere may
> talk to the inverter.

## What the tablet needs

- **Android 7.0 or newer** with an ARM processor (almost every tablet has one, 32 or 64 bit).
- **Always on the charger and in the Wi-Fi at home.** The tablet is the server now: when it is off, empty or out of
  the house, OpenAmpere reads nothing. The screen may be off.
- At least 500 MB of free storage for the app and the readings.
- Modbus TCP switched on at the inverter. The Ampere.StoragePro systems sold by EKD have it on already; otherwise your
  installer can do it.

**Battery:** a tablet that stays on the charger for years can get a swollen battery. If your tablet has it, switch on
battery protection (for example "Protect battery" or "Limit charging to 85 %", in the battery settings depending on the
manufacturer). If the case gets thicker or the screen lifts, unplug the tablet and stop using it.

## Installation

1. **Download:** on the tablet, open the
   [latest version of OpenAmpere](https://github.com/Gr33ndev/OpenAmpere/releases/latest) in the browser and tap the
   file under "Assets" that ends in `.apk` (`openampere-<version>.apk`).
2. **Install:** tap the downloaded file. The first time, Android asks whether the browser may install apps: allow it in
   the setting that opens, then tap **Install**. Google Play Protect does not know the app and may ask; then tap
   **Install anyway**.
3. **Open:** start OpenAmpere. The app asks once whether it may show notifications (from Android 13) and whether it may
   be left out of battery optimisation. **Allow both.** Without that exception Android puts OpenAmpere to sleep and
   readings go missing.
4. **Set up:** choose a password. The setup assistant then looks for the inverter in your home network, or you enter
   its IP address.

While OpenAmpere runs, Android shows a permanent notification "OpenAmpere" with the text "Liest deine Solaranlage
rund um die Uhr …" (reads your solar system around the clock; in English on an English tablet). It has to stay: it tells
Android that the app is needed. Don't switch off the category "OpenAmpere läuft" (OpenAmpere is running) in the
notification settings of OpenAmpere. After the tablet restarts, OpenAmpere starts by itself.

## Opening it from other devices

On a phone or computer in the same Wi-Fi, open `http://<address of the tablet>:8080`. The tablet's address is in its
Wi-Fi settings (under "IP address" for your network). To keep it the same, give the tablet a fixed address in your
router. On an iPhone you can add the page to the home screen.

Other apps such as [evcc](https://evcc.io) or Home Assistant connect to the encrypted address
`https://<address of the tablet>:8443`, as on the other ways.

## Keeping it running

Some manufacturers stop apps in the background despite the battery optimisation exception. Then hours are missing in
the history. Check:

- **Settings → Apps → OpenAmpere → Battery:** "Unrestricted".
- **Samsung:** Settings → Battery → Background usage limits: add OpenAmpere to "Never sleeping apps".
- **Xiaomi, Huawei and others:** also allow "Autostart" for OpenAmpere.

The menus are named a little differently on each tablet. Don't swipe OpenAmpere away from the list of recent apps if
your tablet stops the app completely then.

## What is different on the tablet

- **Updates** are installed by the app itself, see [Updates](#updates).
- **Access from outside:** install the [Tailscale](https://tailscale.com) app on the tablet and on your phone and sign
  in with the same account in both. Then open the Tailscale address of the tablet with port 8080 on the phone. The
  Tailscale helper of way 1 does not exist on the tablet.
- **Gaps in the history:** while the tablet was off or empty, readings are missing. OpenAmpere does not count that as a
  power cut. The daily values are still right, because they come from the inverter's counters.
- **Another network:** if you take the tablet along, OpenAmpere may find another device at the inverter's address. It
  notices that by the serial number and neither reads nor controls that device. Back in the home network it carries on
  by itself.

## Control when the tablet fails

When OpenAmpere controls the inverter continuously, for example for grid charging, it ends by itself if the tablet
fails:

- **FoxESS:** OpenAmpere uses the inverter's remote control with a time limit of 3 minutes. After that the inverter
  goes back to its own mode.
- **SAJ** (the older Ampere.StoragePro): OpenAmpere charges through a charge time slot in the inverter that ends by
  itself after at most 10 minutes. Your own values for this slot are put back as soon as OpenAmpere runs again.

Settings OpenAmpere changed permanently (for example the backup reserve) stay until OpenAmpere runs again.

When OpenAmpere controls a **FoxESS wallbox**, the wallbox charges on with 6 A if the tablet fails, until the car is
full or you stop charging at the wallbox. It is the same with a server.

## Updates

OpenAmpere shows new versions as on the other ways ("Version … ist da"). A tap on **Aktualisieren** (Update) installs
it, at night by itself if you want. The app downloads the new version from GitHub only, checks its checksum and its
signature and then installs it.

- **For the first update** you allow OpenAmpere once to install apps: a notification leads to the setting. Then
  Android asks once more ("Update auf Version … bereit", then **Install**).
- **From Android 12** OpenAmpere installs further updates without asking. On older Android you tap the notification
  and then **Install** each time.
- After the update OpenAmpere starts again by itself. If the app was in front, from Android 10 the notification
  "Version … ist installiert" leads back to it.

Going back to an older version only works on Android by uninstalling, which loses the data. So download a backup
before uninstalling. If you prefer to follow updates with [Obtainium](https://github.com/ImranR98/Obtainium), add
`https://github.com/Gr33ndev/OpenAmpere` there.

## Connecting Home Assistant

The OpenAmpere integration for Home Assistant connects to the tablet as to a server (way 1): install it through HACS,
add it and enter the tablet's address. The steps are in the README under
[Connect Home Assistant](../README.md#connect-home-assistant).

## Backup

Under **Mehr → Daten & Sicherung** (More → Data & backup), **Datensicherung herunterladen** downloads the complete
database with all readings and settings. On the tablet Android asks where to save the file. Better download the backup
from a computer in the home network and keep it there: if the tablet breaks, the backup would be gone as well.

## Moving

From Docker or the Home Assistant app to the tablet:

1. Download a backup from the existing OpenAmpere under **Mehr → Daten & Sicherung**.
2. **Stop** the existing OpenAmpere (Docker: `sudo docker compose down` in the installation folder, Home Assistant:
   stop the app).
3. Install the app on the tablet and choose a password. You don't need the setup assistant.
4. Under **Mehr → Daten & Sicherung**, pick the file at **Sicherung wiederherstellen** (restore backup).

Passwords and keys for other services (evcc, ntfy, customer portal, local key of a wallbox) are not in the backup;
enter them again on the tablet. Connected apps such as Home Assistant need the new address.

The other way round works the same: download a backup on the tablet, stop or uninstall the app, restore the backup in
the new OpenAmpere.

## Uninstalling

**Think first:** settings OpenAmpere changed at the inverter stay active there, see
[Uninstall in the README](../README.md#uninstalling). Uninstalling the app makes Android delete all readings. Download a
backup first if you want to keep them.

## Checking the origin

Every version is signed with the project's key. Android installs an update only when it is signed with the same key as
the installed app. The SHA-256 fingerprint of the certificate is:

```
26952e8da7f7093cf8ea99622d1576c88d075896b851e8ba4ff792f109854886
```

To check by hand you need a computer with the Android SDK: `apksigner verify --print-certs openampere-<version>.apk`
must show this value at "Signer #1 certificate SHA-256 digest". Every file of a release also has a signed build
provenance, which the [GitHub CLI](https://cli.github.com) checks:

```bash
gh attestation verify openampere-<version>.apk --repo Gr33ndev/OpenAmpere \
  --signer-workflow Gr33ndev/OpenAmpere/.github/workflows/release.yml --source-ref refs/tags/v<version>
```

## Problems

- **"Port 8080 wird schon von einer anderen App auf dem Tablet benutzt"** (port 8080 is used by another app): close or
  uninstall that app and restart the tablet, as the message says.
- **"OpenAmpere läuft nicht"** (OpenAmpere is not running): OpenAmpere failed to start several times in a row. The app
  shows the reason and tries again the next time you open it. If restarting the tablet does not help, please open an
  [issue](https://github.com/Gr33ndev/OpenAmpere/issues) with the text the app shows.
- **The inverter is not found:** see [Troubleshooting in the README](../README.md#troubleshooting).
