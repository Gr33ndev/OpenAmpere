# Volkswagen group EU Data Act portal (cars)

> **Not official information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No affiliation
> with the Volkswagen group or its brands, not legal advice. See [Device knowledge](../README.md).

**Applies to:** cars of Volkswagen, Audi, Škoda, SEAT and CUPRA with an account at the brand, through the group's EU
Data Act portal `eu-data-act.drivesomethinggreater.com` · **Sold as:** – · **OpenAmpere:** car source `vw_euda`,
`src/openampere/cars/vw_euda.py` · **Support:** reading state of charge, range, odometer, plug and charging state
(read-only) · **Confirmed on a real device:** no (built from evcc's client; not yet tested against the real portal by
the maintainers, [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401))

## Summary

- Since June 2026 the brands' app interfaces only answer the manufacturer's own apps. The EU Data Act portal is the
  remaining way for owners to get their car's data without a paid service.
- It is not a live interface: about every 15 minutes the portal stores a dataset (a zip archive with one JSON
  document of data points), some 15 minutes after the values were taken. Only reading, no control, no position.
- The owner has to do one step in a browser first: log in, give consent, link the car and start a continuous data
  request. Without it the portal delivers nothing; the first data can take hours.
- The login is the web login of the brand account. There is no token that lasts, so the password is kept.
- The same portal and the same endpoints serve all five brands; only the identity client of the login differs.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| The app interfaces refuse other clients since early June 2026: the CUPRA/SEAT interface asks for an app-check token only the official app can create (HTTP 403), Volkswagen and Audi a client assertion. | derived | WulfgarW/pycupra (README); evcc-io/evcc#30324 |
| The login is an OIDC authorization code flow at `identity.vwgroup.io` with the portal's client id per brand (CUPRA and SEAT share one), redirect `…/login` of the portal, scope `openid cars profile`, `prompt=login`. A form `emailPasswordForm` takes the e-mail, then a page with the script object `window._IDK` gives the values for the password step. The redirects end on the portal's user page `/content/euda/<country>/<language>/user.html` with the session cookies. | derived | evcc-io/evcc `vehicle/vw/eudataact`, based on TA2k/ioBroker.vw-connect |
| After the login the identity service sometimes shows a marketing consent page (`/consent/marketing/…`). Its `callback` parameter continues the login without consenting. | derived | evcc-io/evcc (vwidentity, #29760 there) |
| Before the owner gave consent in the portal, the login ends on a sign-in, consent or error page instead of the user page. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| `GET /proxy_api/consent/me/vehicles` lists the linked cars with VIN and nickname or model, as a list or under `vehicles`. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| `GET /proxy_api/euda-apim/datarequest/vehicles/<VIN>/metadata/partial` gives the `Identifier` of the data request; 404 while none was started. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| `GET …/datadelivery/vehicles/<VIN>/<identifier>/list` (header `type: partial`) lists the datasets with `name` and `createdOn`; 404 ("No files available") until the first one was delivered. `…/download` with the header `filename` gives the zip archive. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| While the car sleeps the portal stores placeholders named `…_no_content_found.zip`. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| A dataset is a JSON document `{"vin": …, "Data": [{"key", "dataFieldName", "value", "timestampUtc"}]}`. `key` is a GUID, `dataFieldName` a name or something generic like `value`. The `timestampUtc` of the points is not reliable; the time of the dataset counts. | derived | evcc-io/evcc `vehicle/vw/eudataact` (types.go, store.go) |
| State of charge: `battery_level_HV.value`, `state_of_charge`, `hv_soc` (plug-in hybrids), for the ID.3 the GUID `506cb83e-f99f-3af3-bbeb-0429b69a78d9`, for the Enyaq `0a18a053-b4b0-3db1-be44-a6c5dba629b1`. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| Range: `cruising_range_secondary_engine`, `cruising_range_primary_engine`, `cruising_range_combined`, for the ID.3 the GUID `0ca40e18-0564-3eda-bcc0-7aee9ef44f04`. Odometer: `mileage`, `mileage.value`. | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| Plug: `plug_state`, `charging_plug1_connectionstate` (`connected`). Charging: `charging_state` / `charging_state_report.current_charge_state` (`charging`, `conservationCharging`, `…CHARGING_HV…`); `charging_state_report.charging_scenario` ending in `_ACTIVE` (charging) or `_FINISHED` (plugged in, not charging). | derived | evcc-io/evcc `vehicle/vw/eudataact` |
| Target state of charge: `settings.target_soc`; on some Audi models `battery_care_mode.charge_bcam_threshold`, but only while `setting.bcam_activation` is `BCAM_ACTIVATION_ACTIVATED`. | derived | evcc-io/evcc `vehicle/vw/eudataact` (#32520 there) |
| Owners report datasets without content for hours, a long delay of the state of charge on some models and a service outage in June 2026. | derived | evcc-io/evcc#30362, WulfgarW/ha_get_euda_data |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Read the portal at all, with the owner's login, as an optional, read-only source. | The owner's own data through the channel the EU Data Act gives them; an AC wallbox cannot know the state of charge. Decided as an exception to "cloud-free". | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Keep the password encrypted with `secret.key` (a backup holds it encrypted, unreadable without `secret.key`, which OpenAmpere's own backups do not contain; a Home Assistant backup of the app holds both, as for every stored secret), never show it; the VIN only as its last four characters in the app and not in reports or logs. | The portal has no lasting token; the VIN identifies the car and its owner. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Follow login redirects only between the identity service and the portal, and accept addresses built from the login page (form action, password step) only on the identity service; never fetch the user page or the marketing page, give no consent. | Credentials and cookies go nowhere else; consent is the owner's decision. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Look again a minute after the next dataset is due (its time + 16 minutes), at least 2 minutes apart; every 10 minutes when it is overdue, every 30 minutes before the first one, hourly while the owner still has a step to do in the portal. Download only datasets newer than the last one, at most a day at once; eight at the first look. | Goes easy on the portal while new values arrive soon after they are there. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| After the portal refused the login, do not retry by itself until the login is changed or the owner asks for the data. | Repeated failed logins could lock the owner's account. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Store every delivery in OpenAmpere's own database and take the state of charge at plug-in and the odometer of a charge from it when they are shown: the last value up to 12 hours before the plug-in, otherwise the first up to 45 minutes after it; at the end the last value up to the unplugging, otherwise the first after it. | The portal delivers late and keeps data only for a while; a parked car keeps its state of charge. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Keep the deliveries 90 days (about 100 rows a day); an ended charge keeps its state of charge at plug-in and at the end and its odometer (settled a day after it ended, when late deliveries are in) until the car is removed; the data of cars no longer set up is deleted; never log the car's name, login or VIN (the log is part of the diagnostics report), and pseudonymise them in reports through `privacy.py`. | The analysis takes the state of charge at plug-in and the odometer from them; the log is shared in issues. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| A charge is a plug-in at the wallbox OpenAmpere controls and that is linked to the car; its energy is what the wallbox counts. The wallbox control does not use the car's values. | Only reading for now; control by state of charge would be its own, safety-relevant issue. | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |
| Send an honest user agent that names OpenAmpere. | As for the grid meter portal. | [#174](https://github.com/Gr33ndev/OpenAmpere/issues/174) |
| Take only facts from other projects, no code. | License and own words; credited in [NOTICE](../../../NOTICE). | [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401) |

## Open questions

- Nothing above was checked against the real portal by the maintainers. A report from an owner with a car of the
  group is needed: does the login work as described, which data points does their model deliver, how late are they?
- Whether the portal turns away clients that do not look like a browser (the honest user agent).
- Whether a car charged somewhere else should be told apart from a guest car at the same wallbox (today every
  plug-in at the linked wallbox counts as this car).
