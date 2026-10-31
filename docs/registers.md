# Modbus registers

The register tables, with the status and the source of every register, are on the device pages in
[docs/devices/](devices/README.md). This page only shows where to find them.

| Device | Register map in OpenAmpere | Registers |
|---|---|---|
| FoxESS H3, H3 Smart, H3 Pro, newer firmware | `foxess_h3_new` (unit 247) | [devices/foxess/h3-new-map.md](devices/foxess/h3-new-map.md) |
| FoxESS H3, older firmware | `foxess_h3_legacy` (unit 247) | [devices/foxess/h3-legacy-map.md](devices/foxess/h3-legacy-map.md) |
| FoxESS H3: connection, detection, rules for writing, remote control | both | [devices/foxess/h3.md](devices/foxess/h3.md) |
| FoxESS AC EV Charger (read-only check) | wallbox check | [devices/foxess/ac-charger.md](devices/foxess/ac-charger.md) |
| SAJ H2 / HS2 / AS2: measurements, settings, control, charge slots | `saj_h2` (unit 1 or 2) | [devices/saj/h2-family.md](devices/saj/h2-family.md) |
| SAJ type code 0x005E ("3P-X E2") | `saj_h2` | [devices/saj/0x005e-3p-x-e2.md](devices/saj/0x005e-3p-x-e2.md) |
| Solarmax MAX.STORAGE ("AMPERE.Storage"), display only | `solarmax_max_storage` (unit 1, FC04) | [devices/solarmax/max-storage.md](devices/solarmax/max-storage.md) |
| Senec.Home V3 / V2.1: the keys of its web interface `lala.cgi`, display only | `senec_lala` (HTTP) | [devices/senec/home-v3.md](devices/senec/home-v3.md) |
| Weidmüller AC SMART ("AMPERE.Charge"), display only | wallbox module `weidmueller_ac_smart` (unit 255) | [devices/weidmueller/ac-smart.md](devices/weidmueller/ac-smart.md) |
| M-TEC Energy-Butler GEN3, display only | `mtec_gen3` (units 252, 255, 247) | [devices/mtec/energy-butler.md](devices/mtec/energy-butler.md) |
| Enphase IQ Gateway with IQ Batteries: endpoints and fields of its local API, display only | `enphase_local` (HTTPS, token) | [devices/enphase/iq-gateway.md](devices/enphase/iq-gateway.md) |
| my-PV AC ELWA heating rod | registers 1000–1003 | [devices/mypv/elwa.md](devices/mypv/elwa.md) |
| Previous EKD smartbox: what it writes | — | [devices/topics/iq-box.md](devices/topics/iq-box.md) |

The sources of all register facts are named on these pages and, for projects whose facts OpenAmpere uses, in
[NOTICE](../NOTICE).
