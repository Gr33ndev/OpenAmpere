Deutsch: [evcc.de.md](evcc.de.md)

# Wallbox with evcc

This page is about wallboxes controlled by [evcc](https://evcc.io), an independent open-source
project for solar charging (MIT license, [GitHub](https://github.com/evcc-io/evcc)). evcc supports a great many
wallboxes and vehicles, charging plans, dynamic tariffs and charging sessions with billing. We do not rebuild that,
we use it. Thanks to the evcc community!

## Costs: the evcc sponsor token

evcc is free software, but for many commercial wallboxes evcc requires a **sponsor token**. This is how the evcc
project is funded: a yearly or one-time contribution, current prices on the
[evcc sponsorship page](https://docs.evcc.io/en/sponsorship). Open-hardware wallboxes, wallboxes of manufacturers that
support evcc, smart plugs and plugins work without a token.

- **Does my wallbox need one?** The [evcc wallbox list](https://docs.evcc.io/en/chargers) marks every wallbox with
  "sponsoring" (token needed) or "sponsor free" (no token). The filter "sponsor free" shows only those without.
- **Test token:** evcc publishes a free test token on its sponsorship page. It is meant for trying evcc out and is
  valid for only about ten days. After that evcc no longer starts a wallbox that needs a token. A new test token is
  published regularly, but copying one every few days is not meant as a permanent solution.
- **Paid token:** also has an expiry date inside; you get a new one on [sponsor.evcc.io](https://sponsor.evcc.io), and
  evcc warns 30 days ahead.
- **In OpenAmpere:** under **Geräte** (Devices) and **Mehr → Verbindung → Wallbox** OpenAmpere warns while evcc runs
  with the test token or the token expires soon, says when the token has expired (#462), and says so when evcc does not start the wallbox because a valid
  token is missing. Next to a wallbox OpenAmpere reads directly, while evcc has no charge point, it says instead: if it
  is that wallbox, it needs no token and should be deleted in evcc; if evcc controls another wallbox, enter a valid
  token (#535). See [Wallbox without evcc](wallbox-direct.md).

We recommend supporting evcc. OpenAmpere uses only the open interface of evcc and has no influence on its terms.

## How the two work together

```
Inverter ── Modbus TCP ──▶ OpenAmpere ── /api/evcc/site ──▶ evcc ──▶ Wallbox
                               ▲                               │
                               └──── evcc REST API ◀───────────┘
                               Display and control in the app
```

- **evcc gets its readings from OpenAmpere.** evcc reads grid, solar and battery from `/api/evcc/site`. That way
  evcc does not need its own connection to the inverter. Many inverters allow only a few simultaneous Modbus
  connections.
- **OpenAmpere shows and controls the charge points.** Charging mode (Off, Solar, Min + Solar, Fast), charging
  target and a charging plan up to a given time are under **Geräte** (Devices). The energy flow, daily values and
  **Auswertung** (Analysis) also show the charging, and you will find the charging sessions in Auswertung.
- **Surplus is shared.** Under **Geräte** you set in a list who gets solar power first: battery (up to a state of
  charge), wallbox, immersion heater and other devices. If the battery comes before the wallbox, OpenAmpere sends
  that state of charge to evcc as **Vorrang Hausspeicher** (home battery priority, "priority SoC").

## The car in evcc

Many cars can be set up as a vehicle in evcc, for example VW, Cupra, Skoda, Tesla, BMW or Renault
([list at evcc](https://docs.evcc.io/de/vehicles), some only with sponsorship). evcc then queries the state of
charge through the manufacturer's cloud, usually only while the car is plugged in. For cars of the Volkswagen group
(VW, Audi, Skoda, SEAT, CUPRA) this now goes through the group's EU Data Act portal, which needs a one-time setup in a
browser and delivers values at least 15 minutes old; OpenAmpere can also read that portal itself, see
[car.md](car.md). This lets OpenAmpere do more:

- **State of charge and range** in the energy flow and on the wallbox card, plus **Ziel erreicht um 15:40 Uhr**
  (target reached at 15:40).
- **Charging to 80 %** and charging plans like "80 % by 7:00". Without a vehicle, evcc only knows the kWh charged.
- **Minimum charge:** Up to this state of charge the car charges immediately, even with grid power; after that the
  charging mode applies. This is a vehicle setting in evcc; OpenAmpere sets it under **Geräte**.
- **"Auto" (car) analysis:** evcc stores the odometer reading with every charging session. From this OpenAmpere
  calculates kilometers driven, kilometers on solar power, consumption per 100 km and cost per 100 km, for 30 days,
  12 months or in total. Solar power is counted at the feed-in tariff you missed out on, grid power at the price from
  your electricity tariff in OpenAmpere. If you also charge away from home, that energy is missing and consumption
  looks too low.

## Setup

evcc gets its readings from OpenAmpere. So set up OpenAmpere first, then evcc.

1. Install evcc. The OpenAmpere install script does this if you answer yes to the wallbox question. For a manual
   installation see below. If evcc is already running, for example on another computer, it can stay that way.
2. Enter the OpenAmpere meters in evcc. The app shows the ready-made configuration with the correct address for
   copying under **Mehr → Verbindung → Wallbox** (More → Connection → Wallbox). In the evcc web interface, add it
   as a user-defined device, or alternatively put it in `evcc.yaml`. It looks like this, with the address you opened
   OpenAmpere under in place of `localhost`:

   ```yaml
   meters:
     - name: openampere_grid
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .grid_power
       energy:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .grid_import_kwh
     - name: openampere_pv
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .pv_power
     - name: openampere_battery
       type: custom
       power:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .battery_power
       soc:
         source: http
         uri: http://localhost:8080/api/evcc/site
         jq: .battery_soc

   site:
     meters:
       grid: openampere_grid
       pv: [openampere_pv]
       battery: [openampere_battery]
   ```

   The signs match without conversion: grid positive when importing, battery positive when discharging. If
   OpenAmpere has no current values, the address responds with an error, and evcc does not charge based on stale
   values.
3. Set up the wallbox and vehicle directly in evcc.
4. In OpenAmpere, enter the evcc address under **Mehr → Verbindung → Wallbox**; on the same computer that is
   `http://localhost:7070`. A password is only needed if evcc requires a login for changes.

## evcc with Docker next to OpenAmpere

OpenAmpere's `docker-compose.yml` already contains evcc as a commented-out second service:

```yaml
  evcc:
    image: evcc/evcc:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./evcc:/root/.evcc
```

First create the `evcc/` folder so that only root can read it, because evcc also stores passwords there:

```bash
sudo mkdir -p evcc && sudo chown root:root evcc && sudo chmod 700 evcc
```

Then uncomment the lines and run `docker compose up -d`. evcc stores its settings in the `evcc/` folder and is
reachable at `http://<server-ip>:7070`. Set a password for evcc there right away: until then, anyone in the home
network can set it and change evcc. Both containers use the host network, so evcc reaches OpenAmpere at
`http://localhost:8080` and OpenAmpere reaches evcc at `http://localhost:7070`. If you prefer to configure evcc with
a file, also mount `./evcc.yaml:/etc/evcc.yaml`, see the
[evcc Docker guide](https://docs.evcc.io/en/installation/docker).

## FoxESS AC EV Charger (also the wallbox of EKD systems)

The wallbox EKD sold with its Ampere.IQ systems is a FoxESS AC EV Charger: in the router and in OpenAmpere's search it
shows up as `EVC-…`. In evcc it is set up with the template **FoxESS** (`foxess-evc`), which needs a sponsor token.

- In the **Fox Switch** app, set the wallbox's communication mode to **Modbus TCP**. In the default mode Modbus is
  read-only according to FoxESS, so evcc cannot control the wallbox; some firmware refuses even reading ("server
  device busy").
- `connection reset by peer` in evcc means the wallbox closed the connection right away. The cause is not known yet;
  possible are a communication mode other than Modbus TCP, or another program that already holds the wallbox's
  connection (OpenAmpere assumes the wallbox allows only one). Run only one program that reads the wallbox.
- Register lists for other wallbox makes (for example holding registers 100, 300 and 400) do not fit this wallbox. A
  "custom" charger built from them in evcc ran into timeouts for one owner.

Without evcc, OpenAmpere can check the wallbox read-only, see [wallbox-direct.md](wallbox-direct.md#checking-other-wallboxes-read-only).

## A message on your phone when charging

Under **Mehr → Benachrichtigungen** (More → Notifications), **Laden der Wallbox** (Wallbox charging) sends a message
when a car starts charging at an evcc loadpoint and when charging ends, with the energy and the solar share since
plugging in, as evcc counts them. A pause of less than 15 minutes does not count as the end. Loadpoints for a heat pump or a heating rod
are not reported.

## Limitations

- Some devices can only be used in evcc with a [sponsorship](https://docs.evcc.io/docs/sponsorship). That is evcc's
  business and supports its development.
- OpenAmpere controls the my-PV immersion heater itself (**Mehr → Verbindung → Heizstab und weitere Geräte**, More →
  Connection → Immersion heater and other devices; operated under **Geräte**). If it is set up in evcc instead,
  OpenAmpere shows it as an evcc charge point.
- evcc's internal interface may change between versions. OpenAmpere reads it fault-tolerantly and uses commands
  that both old and new evcc versions understand.
