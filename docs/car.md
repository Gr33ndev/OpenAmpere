# The car's state of charge from its manufacturer

Deutsch: [car.de.md](car.de.md)

A wallbox does not know how full the car is. Only the car does, and its manufacturer. OpenAmpere can fetch the
**state of charge**, the **range** and the **odometer** from the manufacturer. So far this works for cars from
**Volkswagen, Audi, Škoda, SEAT and CUPRA** through the Volkswagen group's EU Data Act portal.

While the car is plugged in at a wallbox OpenAmpere controls itself ([wallbox-direct.md](wallbox-direct.md)), the
wallbox shows its state of charge. Every charge is stored, with the state of charge at plug-in, the energy charged
and the kilometres in between. Under **Auswertung → Auto** (Analysis → Car) OpenAmpere calculates the consumption
per 100 km from it.

If your wallbox runs through evcc and the car is set up there as a vehicle, you do not need this: evcc already
delivers state of charge and range ([evcc.md](evcc.md)).

> The values are never live: the portal delivers about every 15 minutes, and the values are then at least 15 minutes
> old. OpenAmpere shows how old every value is. OpenAmpere only reads. It sends nothing to the car, and the wallbox
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
without the key, and the app never shows it. If the portal refuses the login, OpenAmpere does not try again by itself (your account could be
locked otherwise). Change email or password then, or tap **Jetzt abrufen** (Fetch now).

## Good to know

- **How OpenAmpere counts charges:** every plug-in at the linked wallbox is a charge of this car. If another car
  charges there once, it counts too.
- **State of charge at plug-in:** the portal delivers late. OpenAmpere therefore takes the last value before the
  plug-in (a parked car keeps its state of charge), otherwise the first one after it.
- **Consumption:** if you also charge away from home, that energy is missing and the consumption looks too low.
- **What is kept:** OpenAmpere keeps the manufacturer's values and the charges until you remove the car. The
  analysis needs them for the state of charge at plug-in and the kilometres driven.
- **The portal is not an official interface for apps.** If the manufacturer changes the login, fetching can fail
  until OpenAmpere is adapted. Please open an issue then, with the report from **Mehr → Diagnose** (More →
  Diagnostics); it contains no login and no VIN.
- OpenAmpere is not part of the Volkswagen group. What is known about the portal:
  [devices/cars/vw-eu-data-act.md](devices/cars/vw-eu-data-act.md).
