# BMW CarData (BMW and MINI cars)

> **Not official information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No affiliation
> with the BMW Group or its brands, not legal advice. See [Device knowledge](../README.md).

**Applies to:** BMW and MINI cars with a "My BMW" or "MINI" account, through BMW CarData, the BMW Group's offer to
owners to read their car's data (EU Data Act) · **Sold as:** – · **OpenAmpere:** car source `bmw_cardata`,
`src/openampere/cars/bmw_cardata.py` · **Support:** reading state of charge, range, odometer, plug and charging state
and the charge limit (read-only) · **Confirmed on a real device:** no (built from BMW's public CarData documentation
and evcc's client; not yet tested with a real car by the maintainers,
[#595](https://github.com/Gr33ndev/OpenAmpere/issues/595))

## Summary

- BMW's own app interface is closed to other clients. BMW CarData is the way BMW offers owners their car's data
  without a paid service: a REST interface (50 requests a day) and a live data stream (MQTT).
- The owner does a few one-time steps in the BMW or MINI web portal: create a **client ID**, request access to the
  **CarData API**, and confirm OpenAmpere's login with a code (OAuth device code flow). No password is given to
  OpenAmpere.
- The login gives a refresh token that lasts **two weeks** and is renewed with every use. If OpenAmpere does not run
  for longer than that, the owner has to confirm a new code.
- OpenAmpere reads only the REST interface, about every 40 minutes. The data stream would need an MQTT client
  (a new dependency) and is left for later.
- MINI uses the same CarData service, reached from the MINI portal; the client ID is created there.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| CarData is offered in the customer portals of BMW ("My BMW", e.g. `bmw.de/de-de/mybmw/vehicle-overview`), MINI (`mini.de/de-de/mymini/vehicle-overview`), Toyota Supra and Rolls-Royce. The car must be mapped to the account, and only the **primary** user of a car gets its data. | documented | BMW CarData public API documentation (`bmw-cardata.bmwgroup.com/customer/public/api-documentation`); kvanbiesen/bmw-cardata-ha (README) |
| The owner creates a client ID in the portal ("Create CarData Client") and requests access to the CarData API (and, for the stream, CarData Stream). The subscription has to come before the login with the client ID. The portal shows the client ID only right after it was created. | documented | BMW CarData documentation; kvanbiesen/bmw-cardata-ha (README) |
| Login: OAuth 2.0 device authorization grant with PKCE (S256). `POST https://customer.bmwgroup.com/gcdm/oauth/device/code` with `client_id`, `response_type=device_code`, `scope=authenticate_user openid cardata:api:read` (plus `cardata:streaming:read` for the stream), `code_challenge`, `code_challenge_method=S256` gives `user_code`, `device_code`, `verification_uri(_complete)`, `interval`, `expires_in`. The owner opens the address, logs in and confirms the code. | documented | BMW CarData documentation; evcc-io/evcc `vehicle/bmw/cardata` (oauth2.go) |
| `POST https://customer.bmwgroup.com/gcdm/oauth/token` with `grant_type=urn:ietf:params:oauth:grant-type:device_code`, `client_id`, `device_code`, `code_verifier` gives `access_token` (1 hour), `refresh_token` (2 weeks), `id_token` (1 hour, for the stream) and `gcid` (the account). Before the confirmation it answers like RFC 8628 (`authorization_pending`). | documented | BMW CarData documentation |
| `grant_type=refresh_token` with `refresh_token` and `client_id` gives a new set of all three tokens and restarts their lifetimes. After the refresh token expired, or the client ID lost its subscription, the device flow has to be run again. | documented | BMW CarData documentation |
| The token endpoint sometimes answers HTTP 500 `keymanagement.service.invalid_access_token` after the owner confirmed the code. Deleting the client ID in the portal and creating a new one helped. | derived | evcc-io/evcc#26347 |
| REST: `https://api-cardata.bmwgroup.com`, header `x-version: v1`, `Authorization: Bearer <access token>`. **50 requests a day** per account (error `CU-429`); BMW recommends the stream for more. | documented | BMW CarData documentation; evcc-io/evcc `vehicle/bmw/cardata` (api.go, cardata.yaml) |
| `GET /customers/vehicles/mappings` lists the cars with `vin` and `mappingType` (`PRIMARY`, `SECONDARY`). | documented | BMW CarData documentation; evcc-io/evcc (api.go, types.go) |
| The data is read through a "container" of telematic keys the client creates itself: `GET`/`POST /customers/containers` (`name`, `purpose`, `technicalDescriptors`; answer `containerId`, 13 characters), `DELETE /customers/containers/<id>`. At most 10 containers per account; a deleted one is inactive and goes after 29 days. | documented | BMW CarData documentation; evcc-io/evcc (api.go) |
| `GET /customers/vehicles/<VIN>/telematicData?containerId=<id>` gives `{"telematicData": {<key>: {"timestamp", "unit", "value"}}}`, the last value the car sent for each key, values as text. | derived | evcc-io/evcc (api.go, types.go) |
| The values are as fresh as the car's last report: the car sends when it is used, charges or is woken up (e.g. by locking it in the app). A parked, sleeping car delivers nothing new. | derived | evcc-io/evcc (cardata.yaml); kvanbiesen/bmw-cardata-ha (README) |
| State of charge: `vehicle.drivetrain.batteryManagement.header` (%), otherwise `vehicle.powertrain.electric.battery.stateOfCharge.displayed`. Charge limit: `vehicle.powertrain.electric.battery.stateOfCharge.target`. | derived | evcc-io/evcc (provider.go) |
| Range: `vehicle.drivetrain.electricEngine.kombiRemainingElectricRange` (km), otherwise `vehicle.drivetrain.lastRemainingRange`. Odometer: `vehicle.vehicle.travelledDistance` (km). | derived | evcc-io/evcc (provider.go) |
| Plug: `vehicle.body.chargingPort.status`, otherwise `…combinedStatus` (`CONNECTED`). Charging: `vehicle.drivetrain.electricEngine.charging.status` (`CHARGINGACTIVE`; plugged in but not charging: `INITIALIZATION`, `CHARGINGPAUSED`, `CHARGINGENDED`), otherwise `…charging.hvStatus` (`CHARGING`; `WAITING_FOR_CHARGING`, `FINISHED_FULLY_CHARGED`, `FINISHED_NOT_FULL`). Other values (e.g. `NOCHARGING` after unplugging, `CHARGINGERROR`) mean neither charging nor, by themselves, plugged in. | derived | evcc-io/evcc (provider.go, #26235 there) |
| Stream: MQTT over TLS, `customer.streaming-cardata.bmwgroup.com:9000`, user `gcid`, password the `id_token`, topic `<gcid>/+`; only the keys chosen in the portal. BMW accepts **one** stream connection per account. | derived | evcc-io/evcc (mqtt.go, cardata.yaml); kvanbiesen/bmw-cardata-ha (README) |
| Which keys a given model delivers differs; the telematics data catalogue in the portal lists them. | documented | BMW CarData documentation |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Read BMW CarData at all, as an optional, read-only source, like the Volkswagen group's portal. | The owner's own data through the channel BMW gives them; an AC wallbox cannot know the state of charge. Same exception to "cloud-free" as [#401](https://github.com/Gr33ndev/OpenAmpere/issues/401). | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Only the REST interface, no stream. | The stream needs an MQTT client, a new dependency that has to be agreed first; BMW allows one stream per account, which evcc or Home Assistant may already use. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Look every 40 minutes (36 requests a day), at most once a minute when the owner asks; wait 3 hours after `CU-429`. Create one container named `OpenAmpere` once and keep its id. | Leaves room in the 50 requests for the setup and for asking now; the limit counts for the whole account. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| The 40 minutes are per account, not per car: with two cars of the same login each looks every 80 minutes; an error also waits at least that long. | BMW counts the 50 requests for the whole account. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| For every value with two keys (state of charge, range, plug) the newer one counts, with the same time stamp the first. | A model may have sent one key once and now only update the other. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| The charge state comes from the newer of the two keys (with the same time stamp, the one with a known value); any other value counts as "not charging". Whether the cable is in comes from the plug key; only a charging state newer than it says "plugged in" as well. | Otherwise a car unplugged after a charge would keep showing "charging", and a "finished" state sent again after unplugging would show "plugged in". | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| A `404` for the data looks up the container once more; a second `404` means the car is no longer in the account: a setup step, looked at again after 6 hours. | Retrying every half hour would use up the daily limit with a wrong reason. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Store a value only when the car sent it after the last one OpenAmpere has; values with the same time stamp make one delivery. | A parked car repeats the same values for hours. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Keep the refresh token (not a password) encrypted with `secret.key` like the password of the Volkswagen source, store the new one after every renewal, never show it. The client ID is shown, it is no secret on its own. | The token is the login; BMW renews it with every use. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| The owner confirms the login in their own browser with the code OpenAmpere shows; OpenAmpere asks the token endpoint at the interval BMW names and stops when the code expires. | That is BMW's login for such clients; no password goes through OpenAmpere. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| A refused or expired token is not retried by itself (status "auth"); the app asks to confirm a new code. | Retrying cannot help; it only uses up requests. | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Send requests only to `customer.bmwgroup.com` and `api-cardata.bmwgroup.com`, never follow a redirect with the token. | The token goes nowhere else (`outbound.py`). | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |
| Take only facts from other projects and BMW's documentation, no code. | License and own words; credited in [NOTICE](../../../NOTICE). | [#595](https://github.com/Gr33ndev/OpenAmpere/issues/595) |

## Open questions

- Nothing above was checked with a real car by the maintainers. A report from a BMW or MINI owner is needed: does
  the login work, which keys does their model deliver, how fresh are the values while the car charges?
- Whether a request that fails (e.g. a 4xx) also counts against the 50 a day.
- Whether the stream is worth a dependency: it would bring values within seconds while the car charges.
