> **Deutsch:** Die Anleitung auf Deutsch findest du [auf GitHub](https://github.com/Gr33ndev/OpenAmpere/blob/main/docs/homeassistant-app.de.md).
> **Du hast OpenAmpere schon mit Docker installiert?** Lade dort zuerst eine Sicherung herunter und stoppe die alte
> Installation, bevor du die App startest, siehe [Umzug von Docker](https://github.com/Gr33ndev/OpenAmpere/blob/main/docs/homeassistant-app.de.md#umzug-von-docker).

# OpenAmpere

OpenAmpere is a local app for solar systems with battery storage (FoxESS H3, SAJ H2/HS2). It talks Modbus TCP to the
inverter in your home network and needs no cloud. As a Home Assistant app it runs next to Home Assistant and opens in
the sidebar. The app itself is in German or English (More → Appearance).

The app is new and marked as **experimental**. If something does not work, please
[open an issue](https://github.com/Gr33ndev/OpenAmpere/issues).

## Installation

1. **Add the repository** [with one click](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FGr33ndev%2FOpenAmpere), or by hand: in Home Assistant open **Settings → Apps → Install
   app**, then **⋮ → Repositories**, enter `https://github.com/Gr33ndev/OpenAmpere` and click **Add**.
2. **Install** the app **OpenAmpere**, turn on **Show in sidebar** and **start** it.
3. **Open OpenAmpere** in the sidebar. The setup wizard searches for the inverter in your home network, or you enter
   its IP address.

Modbus TCP must be turned on at the inverter. For the battery systems sold by EKD this is already the case; for other
devices your installer can do it.

In the sidebar you need no password: Home Assistant has already checked who you are. Only **administrators of Home
Assistant** may change settings and control the inverter there; other users of Home Assistant can only view. The
sidebar entry is only shown to administrators. For changes made in the sidebar, the control log (More → Control and
log) shows which user of Home Assistant made them. Changes through the Home Assistant integration show the name of the
app access instead (More → Connected apps), not the user.

## Access by IP address

Besides the sidebar, OpenAmpere can be opened directly at `http://<address of Home Assistant>:8780`, for example on a
phone without Home Assistant or for [evcc](https://evcc.io). This access needs a password. Set it in the sidebar under
**More → Access protection**; until then, direct access is locked. If you forget it, set a new one there. The old one
is not needed.

Browsers send their login cookies to every port of an address. If you open OpenAmpere at the same address as Home
Assistant, Home Assistant and its other apps also receive OpenAmpere's login. So open direct access under a different
address than Home Assistant: if you open Home Assistant by its name (for example `homeassistant.local:8123`), open
OpenAmpere by the IP address, or the other way round. In the sidebar this does not matter.

- **Port in use, or another port:** Settings → Apps → OpenAmpere → **Configuration → Network**. If port 8780 is taken
  by another app, OpenAmpere does not start; choose another port there.
- **Port 8743** is HTTPS for apps on other devices. The Home Assistant integration does not need it.
- **Turn off direct access:** clear both ports there.

## Home Assistant integration

Entities in Home Assistant (power, energy for the Energy dashboard, state of charge and, if you allow it, control of
the battery, charging from the grid, the immersion heater and a wallbox OpenAmpere controls itself) come from the
OpenAmpere integration. **The app brings it along and installs it itself; you need no HACS.** Updates of the app update the integration as well.

1. After the first start of the app, Home Assistant shows a notice: **restart Home Assistant** once (Settings → System
   → Restart).
2. Under **Settings → Devices & services** it then shows **Discovered: OpenAmpere**. Confirm it.
3. Home Assistant and OpenAmpere show the same 6-digit code. Compare it in OpenAmpere under **More → Connected apps**
   and allow the connection, read only or with control. Then confirm the code once more in Home Assistant.

Control entities are only available while control is switched on in OpenAmpere (More → Control and log). With
**Read + control**, every user of Home Assistant and every automation can use them, not only administrators. Home
Assistant cannot limit entities to certain users. If that is not what you want, choose **Read only**.

Had the integration through HACS before? Then the app leaves it alone and says so in a notice. Remove it in HACS and
restart the app; from then on the app keeps it up to date. Details:
[docs/homeassistant.md](https://github.com/Gr33ndev/OpenAmpere/blob/main/docs/homeassistant.md).

## Moving from Docker

Only one OpenAmpere may talk to the inverter. Two of them disturb each other.

1. **In the old installation:** under **More → Data & backup**, download a backup. Then stop it, in a terminal on the
   server: `cd /opt/openampere && sudo docker compose down`
2. **In the app:** go through the setup wizard, then restore the backup under **More → Data & backup → Restore
   backup**. OpenAmpere restarts with your history and settings.
3. **Enter stored credentials again**, for example for the customer portal of the grid operator, ntfy, evcc or the
   Ampere.IQ import. For safety, they are not part of the downloaded backup. The same goes for the local key of a
   wallbox OpenAmpere controls itself: the backup holds it only encrypted with the key of the old installation. The
   wallbox keeps its settings and shows **Local Key neu eintragen** (Enter the local key again) under **More →
   Connection → Wallbox** and on the **Devices** page until you enter the key there.
4. **Home Assistant integration:** if you installed it through HACS, remove it in HACS (not in Devices & services)
   and restart the app; the app installs its own. Home Assistant recognises your OpenAmpere and switches to the app by
   itself. It only asks you to pair once more. Entities and their history stay.
5. **evcc:** if evcc reads the meters from OpenAmpere, copy the configuration again under **More → Connection →
   Wallbox**. It contains the new address.

Devices with their own switch addresses (under **More → Connection → Immersion heater and other devices**) keep their
settings. Their addresses often hold the device's password, though, and are in the downloaded backup only encrypted with
the key of the old installation. The device shows **Schaltadressen neu eintragen** (Enter the switch addresses again)
there and on the **Devices** page. Until you enter the addresses again, OpenAmpere does not switch it, not even off.

## Backups and updates

- **Backups** of Home Assistant include OpenAmpere with its history, settings and stored credentials. OpenAmpere
  writes a consistent copy of its database for each backup. Restore it like any other app. Such a backup also contains
  the key for the stored credentials (`secret.key`), so anyone who can open the backup can read them. Only the
  encryption of Home Assistant's backups protects them: keep its encryption key safe and do not share backups.
- **After restoring a backup**, the password and the accesses for apps are again as they were when the backup was
  made, and signed-in devices sign in again. If you changed the password or removed an access under **More → Connected
  apps** since then, do it again. OpenAmpere reminds you of this until you hide the notice.
- **Updates** come through Home Assistant: Settings → Apps → OpenAmpere. You can turn on automatic updates there.
- **Remote access:** open OpenAmpere in the sidebar through the remote access of Home Assistant, for example Home
  Assistant Cloud or the Tailscale app for Home Assistant.

## Uninstalling

First switch off the devices that OpenAmpere switches through a relay, for example a heat pump or an immersion heater:
on the **Devices** page set them to **Off**. When OpenAmpere stops, it sets adjustable devices to 0 W, but a relay it
switched on stays on until OpenAmpere runs again. This also applies when you only stop the app.

Download a backup first under **More → Data & backup** if you might come back: uninstalling the app deletes its
data (history and settings). Then uninstall the app under Settings → Apps → OpenAmpere.

Home Assistant then shows a repair under **Settings → System → Repairs**: "The OpenAmpere app was removed". Confirm
it, and the integration is removed with its devices and entities, including the files the app installed. The history
of the entities stays in Home Assistant. An integration you installed through HACS stays; remove it in HACS.

## Problems

- **Log:** Settings → Apps → OpenAmpere → **Log**.
- **The inverter is not found:** see
  [Troubleshooting](https://github.com/Gr33ndev/OpenAmpere/blob/main/README.md#troubleshooting).

## Notes

OpenAmpere is an independent open-source project. It is not affiliated with Home Assistant, the Open Home Foundation or
Nabu Casa, nor with Energiekonzepte Deutschland (EKD), FoxESS, SAJ or Kiwigrid. Control functions are switched off by
default; anyone who changes settings on the inverter is responsible for complying with the grid connection conditions.
The notes in the app are not legal advice. More in the
[README](https://github.com/Gr33ndev/OpenAmpere/blob/main/README.md#background--legal-notes).
