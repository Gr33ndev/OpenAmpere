# The car's state of charge from its manufacturer

Deutsch: [car.de.md](car.de.md)

A wallbox does not know how full the car is. Only the car does, and its manufacturer. OpenAmpere can fetch the
**state of charge**, the **range** and the **odometer** from the manufacturer. So far this works for cars from
**Volkswagen, Audi, Škoda, SEAT and CUPRA** through the Volkswagen group's EU Data Act portal and for **BMW and MINI**
through BMW CarData ([below](#bmw-and-mini)).

While the car is plugged in at a wallbox OpenAmpere controls itself ([wallbox-direct.md](wallbox-direct.md)), the
wallbox shows its state of charge. Every charge is stored, with the state of charge at plug-in, the energy charged
and the kilometres in between. Under **Auswertung → Auto** (Analysis → Car) OpenAmpere calculates the consumption
per 100 km from it.

If your wallbox runs through evcc and the car is set up there as a vehicle, you do not need this: evcc already
delivers state of charge and range ([evcc.md](evcc.md)).

> The values are never live: the Volkswagen group's portal delivers about every 15 minutes, and the values are then
> at least 15 minutes old; for BMW and MINI OpenAmpere asks about every 40 minutes. OpenAmpere shows how old every value is. OpenAmpere only reads. It sends nothing to the car, and the wallbox
> control does not follow these values.

## What you need

- An account at the brand (the same as in the brand's app, e.g. "My CUPRA") with your car.
- The portal set up once in a browser (below).

## Set up the portal (once, in a browser)

1. Open [eu-data-act.drivesomethinggreater.com](https://eu-data-act.drivesomethinggreater.com) and log in with your
   brand account.
2. Confirm the consent to the data transfer.
3. Link your car to the account if it is not listed there yet.
4. Start a **continuous data request** with data **every 15 minutes**. Without it the portal delivers nothing.

It can take a few hours until the first data arrives in the portal. Until then OpenAmpere keeps asking by itself.

## Set up in OpenAmpere

1. Open **Mehr → Verbindung → Auto** (More → Connection → Car) and tap **Auto hinzufügen** (Add a car).
2. Enter brand, email address and password of the account.
3. Tap **Autos aus dem Konto laden** (Load the cars of the account) and choose your car.
4. Under **Lädt zu Hause an** (Charges at home at) choose the wallbox OpenAmpere controls itself (if you have one).
5. **Speichern** (Save). The car shows up under **Geräte** (Devices).

OpenAmpere stores your password encrypted because the portal has no lasting login. A backup holds it only encrypted,
unreadable without `secret.key` (not part of OpenAmpere's backups; a Home Assistant backup of the app has both), and the app never shows it. If the portal refuses the login, OpenAmpere does not try again by itself (your account could be
locked otherwise). Change email or password then, or tap **Jetzt abrufen** (Fetch now).

## BMW and MINI

BMW and MINI offer their customers the car's data through **BMW CarData**. OpenAmpere needs no password for it: you
create a **client ID** in your brand's portal and confirm OpenAmpere's login once with a code in the browser.

What you need: a BMW or MINI account (as in the "My BMW" or "MINI" app) in which you are the **primary user** of the
car. Only the primary user gets the data.

### Once in a browser

1. Open your brand's portal and log in:
   [bmw.de/de-de/mybmw/vehicle-overview](https://www.bmw.de/de-de/mybmw/vehicle-overview) or
   [mini.de/de-de/mymini/vehicle-overview](https://www.mini.de/de-de/mymini/vehicle-overview). Choose your car.
2. Open **BMW CarData** or **MINI CarData** and request **access to the CarData API**. Only then create the client ID,
   otherwise the login does not work.
3. Create a **client ID** and copy it right away. The portal shows it only this one time.

### In OpenAmpere

1. Open **Mehr → Verbindung → Auto** (More → Connection → Car) and tap **Auto hinzufügen** (Add a car).
2. Choose **BMW** or **MINI** as the brand and enter the client ID.
3. Tap **Anmeldung starten** (Start the login). OpenAmpere shows a link and a code. Open the link, log in and confirm
   the code. OpenAmpere notices the confirmation by itself after a few seconds.
4. Tap **Autos aus dem Konto laden** (Load the cars of the account), choose your car and the wallbox, and **Speichern**
   (Save).

Good to know for BMW and MINI:

- **The login lasts two weeks** and is renewed with every fetch. If OpenAmpere was off for longer (e.g. the tablet),
  confirm it again under **Ändern → Neu anmelden** (Change → Log in again). OpenAmpere stores only this login key,
  encrypted like a password.
- **BMW allows 50 requests a day** for the whole account. OpenAmpere asks about every 40 minutes; two cars of the same account each every 80 minutes. If you also use BMW
  CarData in evcc or Home Assistant, they share these requests; once the daily limit is reached, OpenAmpere waits a
  few hours.
- **New values come only when the car sends some**, for example while driving and charging. A parked car that sleeps
  reports nothing new.
- If the login does not work ("hands out no access"): delete the client ID in the portal, create a new one and enter
  it in OpenAmpere.
- What is known about BMW CarData: [devices/cars/bmw-cardata.md](devices/cars/bmw-cardata.md).

## Good to know

- **How OpenAmpere counts charges:** every plug-in at the linked wallbox is a charge of this car. If another car
  charges there once, it counts too.
- **State of charge at plug-in:** the portal delivers late. OpenAmpere therefore takes the last value before the
  plug-in (a parked car keeps its state of charge), otherwise the first one after it.
- **Consumption:** if you also charge away from home, that energy is missing and the consumption looks too low.
- **What is kept:** OpenAmpere keeps the single values from the manufacturer for 90 days. Every charge keeps its
  state of charge at plug-in and at the end and the odometer until you remove the car; the analysis needs them for
  the consumption.
- **The portal is not an official interface for apps.** If the manufacturer changes the login, fetching can fail
  until OpenAmpere is adapted. Please open an issue then, with the report from **Mehr → Diagnose** (More →
  Diagnostics); it contains no login and no VIN.
- OpenAmpere is part of neither the Volkswagen group nor the BMW Group. What is known about the portal:
  [devices/cars/vw-eu-data-act.md](devices/cars/vw-eu-data-act.md).
