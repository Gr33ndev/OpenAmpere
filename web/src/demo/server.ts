/** Answers the app's API requests inside the browser for the public demo. Nothing leaves the browser,
 *  nothing is saved: every change is refused with a friendly message. */

import { BATTERY_WH, CAR_KWH, dayStart, energyOf, firstDayStart, KWP, stepsBetween, stepsOf, STEP_S, type Energy, type Step } from "./model";

export const DEMO_WRITE_MESSAGE = "Das ist nur die Demo, hier lässt sich nichts ändern. Für deine eigene Anlage installierst du OpenAmpere.";

const RATED_W = 10_000;
const PRICE_CT = 35;
const FEED_IN_CT = 8.11; // EEG rate of the demo plant (9.8 kWp, commissioned 05/2024)
const COMMISSIONED = "2024-05-15";
const BASE_FEE_EUR_MONTH = 13.7;
const INPUT_NAMES = ["Süddach", "Westdach"];
const QUARTER = 900;

const now = () => Date.now() / 1000;
const nextDay = (start: number) => dayStart(new Date((start + 86400 + 7200) * 1000));

function parseDay(value: string | null): Date {
  if (!value) return new Date();
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, m - 1, d || 1);
}

function bounds(period: string, date: string | null): [number, number] {
  const d = parseDay(date);
  if (period === "week") {
    const monday = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7));
    return [monday.getTime() / 1000, new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 7).getTime() / 1000];
  }
  if (period === "month") {
    return [new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000, new Date(d.getFullYear(), d.getMonth() + 1, 1).getTime() / 1000];
  }
  if (period === "year") return [new Date(d.getFullYear(), 0, 1).getTime() / 1000, new Date(d.getFullYear() + 1, 0, 1).getTime() / 1000];
  const start = dayStart(d);
  return [start, nextDay(start)];
}

function bucket(ts: number, resolution: string): number {
  const d = new Date(ts * 1000);
  if (resolution === "15m") return Math.floor(ts / QUARTER) * QUARTER;
  if (resolution === "60m") return new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()).getTime() / 1000;
  if (resolution === "day") return dayStart(d);
  return new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000;
}

function ratios(e: Energy) {
  return {
    autarky: e.load ? Math.max(0, 1 - e.grid_import / e.load) : null,
    self_consumption: e.pv ? Math.max(0, 1 - e.grid_export / e.pv) : null,
  };
}

const round2 = (v: number) => Math.round(v * 100) / 100;

function money(e: Energy, days = 0) {
  const feedIn = (e.grid_export * FEED_IN_CT) / 100_000, grid = (e.grid_import * PRICE_CT) / 100_000;
  const baseFee = (BASE_FEE_EUR_MONTH * 12) / 365 * days;
  return {
    savings_eur: round2((Math.max(0, e.load - e.grid_import) * PRICE_CT + e.grid_export * FEED_IN_CT) / 100_000),
    feed_in_eur: round2(feedIn), grid_cost_eur: round2(grid), base_fee_eur: round2(baseFee),
    net_cost_eur: round2(grid + baseFee - feedIn), incomplete: false,
  };
}

function current(): Step | null {
  const steps = stepsOf(dayStart(new Date()));
  return steps[steps.length - 1] ?? null;
}

function totals(): Energy {
  const e = energyOf(stepsBetween(firstDayStart(), now()));
  return { ...e, pv: e.pv + 18_400_000, load: e.load + 15_100_000, grid_import: e.grid_import + 6_200_000,
    grid_export: e.grid_export + 7_900_000 };
}

let totalsCache: { at: number; value: Energy } | null = null;
function cachedTotals(): Energy {
  if (!totalsCache || now() - totalsCache.at > 300) totalsCache = { at: now(), value: totals() };
  return totalsCache.value;
}

export function snapshot() {
  const s = current();
  if (!s) return null;
  const wobble = (v: number, f = 0.04) => Math.round(v * (1 + (Math.random() - 0.5) * f));
  const pv1 = wobble(s.pv1), pv2 = wobble(s.pv2), house = wobble(s.load, 0.06);
  const pv = pv1 + pv2;
  const battery = s.battery;
  const lifetime = cachedTotals();
  return {
    timestamp: now(),
    pv_power: pv,
    house_power: house,
    grid_power: house - pv - battery,
    battery_power: battery,
    battery_soc: Math.round(s.soc),
    pv_inputs: [
      { power: pv1, voltage: pv1 ? 520 + Math.random() * 20 : 0, current: pv1 ? Math.round((pv1 / 530) * 100) / 100 : 0 },
      { power: pv2, voltage: pv2 ? 380 + Math.random() * 20 : 0, current: pv2 ? Math.round((pv2 / 390) * 100) / 100 : 0 },
    ],
    temperatures: { inverter: s.tInverter, ambient: Math.round((s.tInverter - 9) * 10) / 10, battery: s.tBattery,
      battery_cell_max: s.tBattery + 0.8, battery_cell_min: s.tBattery - 0.6 },
    battery_temperature: s.tBattery,
    battery_soh: 98,
    inverter_state: 2,
    off_grid: false,
    alarms: [0, 0, 0],
    totals: lifetime,
    today: energyOf(stepsOf(dayStart(new Date()))),
  };
}

/** Demo wallbox from the simulation (in the real app this comes from evcc). */
function wallbox() {
  const s = current();
  const charging = !!s && s.car > 0;
  const session = stepsOf(dayStart(new Date())).reduce((sum, x) => sum + (x.car * STEP_S) / 3600, 0);
  return {
    id: 1, title: "Carport", heating: false, mode: "pv", connected: !!s?.carConnected, charging, enabled: charging,
    power_w: s?.car ?? 0, session_wh: Math.round(session), session_solar_pct: 100, vehicle_name: "egolf",
    vehicle_title: "e-Golf", soc: s?.carSoc ?? null, range_km: s ? Math.round(s.carSoc * 2.6) : null, limit_soc: 80, phases: 1,
    pv_action: charging ? "inactive" : "enable", pv_remaining_s: null, remaining_s: charging ? 5400 : null,
    plan_active: false, plan_time: null, plan_soc: null, min_soc: 20, vehicle_capacity_kwh: CAR_KWH,
  };
}

/** Demo heating rod from the simulation. */
function heatingRod() {
  const s = current();
  const power = s?.rod ?? 0;
  return { on: power > 0, power, temperature: s?.water ?? null };
}

function deviceItems() {
  const rod = heatingRod();
  const car = wallbox();
  return [
    { key: "evcc:1", id: 1, source: "evcc", name: "Carport", kind: "wallbox", enabled: true, power_w: car.power_w,
      active: car.charging, on: car.charging, temperature_c: null, target_c: null, status: null, error: null, override: null,
      connected: car.connected, soc: car.connected ? car.soc : null, range_km: car.connected ? car.range_km : null },
    { key: "c:demo1", id: "demo1", source: "openampere", name: "Heizstab", kind: "heating_rod", enabled: true,
      power_w: rod.power, active: rod.on, on: rod.on, temperature_c: rod.temperature, target_c: 60,
      status: rod.on ? "heizt" : (rod.temperature ?? 0) >= 60 ? "Wasser hat Zieltemperatur" : "Bereitschaft",
      error: null, override: null, connected: null },
  ];
}

const DEVICE_LABELS = [{ key: "evcc:1", name: "Carport", kind: "wallbox" }, { key: "c:demo1", name: "Heizstab", kind: "heating_rod" }];

function devices() {
  const today = stepsOf(dayStart(new Date()));
  const h = STEP_S / 3600;
  return { devices: deviceItems(), evcc: { configured: true, error: null }, priority: "wallbox_first",
    today_wh: { "evcc:1": today.reduce((a, x) => a + x.car * h, 0), "c:demo1": today.reduce((a, x) => a + x.rod * h, 0) } };
}

function devicesEnergy(q: URLSearchParams) {
  const period = q.get("period") ?? "day";
  const resolution = q.get("resolution") ?? "60m";
  const [from, to] = bounds(period, q.get("date"));
  const h = STEP_S / 3600;
  const groups = new Map<number, [number, number]>();
  for (const s of stepsBetween(from, to)) {
    const key = bucket(s.ts, resolution);
    const v = groups.get(key) ?? [0, 0];
    groups.set(key, [v[0] + s.car * h, v[1] + s.rod * h]);
  }
  const entries = [...groups.entries()].map(([ts, values]) => ({ ts, values }));
  const totals_wh = [entries.reduce((a, e) => a + e.values[0], 0), entries.reduce((a, e) => a + e.values[1], 0)];
  return { period, resolution, from, to, devices: DEVICE_LABELS, totals_wh, entries };
}

function devicesPower(q: URLSearchParams) {
  const [from, to] = bounds("day", q.get("date"));
  return { from, to, step: STEP_S, devices: DEVICE_LABELS,
    entries: stepsBetween(from, to).map((s) => ({ ts: s.ts, values: [s.car, s.rod] })) };
}

const settings = {
  values: {
    "inverter.driver": "auto", "inverter.host": "wechselrichter.local", "inverter.port": 502, "inverter.unit": 0,
    "inverter.register_map": "auto", "inverter.read_function": "auto", "inverter.poll_interval": 10,
    "inverter.timeout": 3, "inverter.connection_mode": "persistent", "storage.raw_retention_days": 30,
    "control.enabled": true, "control.dry_run": true, "tariff.electricity_price_ct": PRICE_CT, "tariff.feed_in_ct": FEED_IN_CT,
    "tariff.feed_in_auto": true, "tariff.feed_in_full": false, "pv.commissioning_date": COMMISSIONED,
    timezone: "Europe/Berlin", "pv.input_names": INPUT_NAMES, "pv.hidden_inputs": [], "pv.installed_kwp": KWP, "grid.feed_in_rule": "limit_60",
    "notify.ntfy_url": "", "notify.on_unreachable": true, "notify.on_alarm": true, "notify.on_overwritten": true,
    "notify.on_battery_full": false, "notify.on_cheap_power": false, "notify.on_firmware": true,
    "notify.on_battery_health": true, "notify.on_off_grid": true, "battery.capacity_kwh": BATTERY_WH / 1000, "battery.max_charge_kw": 8.5, "updates.check": false, "updates.auto": false,
    "evcc.url": "http://evcc.local:7070", "evcc.priority": "wallbox_first",
    "meter.provider": "netze_bw", "meter.username": "demo@example.org", "meter.meter_ids": [],
  },
  secrets: { "cloud.api_key": { set: false, hint: null }, "notify.ntfy_token": { set: false, hint: null },
    "evcc.password": { set: false, hint: null }, "meter.password": { set: true, hint: null } },
  locked: [],
  revision: 1,
};

function status() {
  const latest = current();
  return {
    version: "Demo", configured: true, connected: true, last_error: null, last_update: latest ? now() : null,
    stale: false, poll_interval: 10, relocated: null, timezone: "Europe/Berlin", web_build: null, clock_wrong: false,
    devices: { grid_charging: false, items: deviceItems() },
    device: { manufacturer: "FoxESS", model: "H3-10.0-Smart (Demo)", serial: "DEMO000001", firmware: "1.50 / 1.20",
      register_map: "foxess_h3_new", driver: "foxess", unit: 247, rated_power_w: RATED_W, supports_control: true },
    control: { enabled: true, dry_run: true },
    firmware: { serial: "DEMO000001", firmware: "1.50 / 1.20", since: dayStart(new Date()) - 41 * 86400,
      history: [{ ts: dayStart(new Date()) - 41 * 86400 + 52_000, old: "1.48 / 1.20", new: "1.50 / 1.20" }] },
  };
}

function energySummary(q: URLSearchParams) {
  const period = q.get("period") ?? "day";
  const [from, to] = bounds(period, q.get("date"));
  const steps = stepsBetween(from, to);
  const e = energyOf(steps);
  return { period, from, to, quarters: Math.ceil(steps.length / 3), partial_since: null, energy_wh: e, ...ratios(e),
    money: money(e, Math.max(0, Math.min(to, now()) - from) / 86400) };
}

function energyTimeline(q: URLSearchParams) {
  const period = q.get("period") ?? "day";
  const resolution = q.get("resolution") ?? "15m";
  const [from, to] = bounds(period, q.get("date"));
  const groups = new Map<number, Step[]>();
  for (const s of stepsBetween(from, to)) {
    const key = bucket(s.ts, resolution);
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const entries = [...groups.entries()].map(([ts, steps]) => ({ ts, ...energyOf(steps), soc: steps[steps.length - 1].soc }));
  return { period, resolution, from, to, entries };
}

function powerTimeline(q: URLSearchParams) {
  const [from, to] = bounds("day", q.get("date"));
  return { from, to, step: STEP_S, entries: stepsBetween(from, to).map((s) => ({ ts: s.ts, pv: s.pv, house: s.load, grid: s.grid,
    battery: s.battery, soc: s.soc })) };
}

function pvInputs(q: URLSearchParams) {
  const period = q.get("period") ?? "day";
  const mode = q.get("mode") ?? "energy";
  const [from, to] = bounds(period, q.get("date"));
  const steps = stepsBetween(from, to);
  const h = STEP_S / 3600;
  const totals_wh = [steps.reduce((a, s) => a + s.pv1 * h, 0), steps.reduce((a, s) => a + s.pv2 * h, 0)];
  if (mode === "power") {
    return { mode, labels: INPUT_NAMES, totals_wh, entries: steps.map((s) => ({ ts: s.ts, values: [s.pv1, s.pv2] })) };
  }
  const groups = new Map<number, [number, number]>();
  for (const s of steps) {
    const key = bucket(s.ts, q.get("resolution") ?? "60m");
    const v = groups.get(key) ?? [0, 0];
    groups.set(key, [v[0] + s.pv1 * h, v[1] + s.pv2 * h]);
  }
  return { mode, labels: INPUT_NAMES, totals_wh, entries: [...groups.entries()].map(([ts, values]) => ({ ts, values })) };
}

function temperatures(q: URLSearchParams) {
  const [from, to] = bounds("day", q.get("date"));
  return { entries: stepsBetween(from, to).map((s) => ({ ts: s.ts, inverter: s.tInverter, battery: s.tBattery,
    cell_max: s.tBattery + 0.8, cell_min: s.tBattery - 0.6 })) };
}

/** Battery health from the simulation: the same numbers the real app derives from the inverter counters. */
function batteryHealth() {
  const totals = cachedTotals();
  const month = stepsBetween(now() - 30 * 86400, now());
  const hottest = month.reduce((a, s) => (s.tBattery > a.tBattery ? s : a), month[0]);
  const coldest = month.reduce((a, s) => (s.tBattery < a.tBattery ? s : a), month[0]);
  const inverter = month.reduce((a, s) => (s.tInverter > a.tInverter ? s : a), month[0]);
  const s = current();
  return {
    capacity_kwh: BATTERY_WH / 1000, charged_kwh: totals.battery_charge / 1000, discharged_kwh: totals.battery_discharge / 1000,
    cycles: totals.battery_discharge / BATTERY_WH, efficiency_pct: (totals.battery_discharge / totals.battery_charge) * 100,
    soh_pct: 98, cell_max_now_c: s ? s.tBattery + 0.8 : null, cell_min_now_c: s ? s.tBattery - 0.6 : null,
    spread_now_c: s ? 1.4 : null, days: 30, warning: null,
    extremes: {
      cell_max: { value: hottest.tBattery + 0.8, ts: hottest.ts }, cell_min: { value: coldest.tBattery - 0.6, ts: coldest.ts },
      spread: { value: 1.9, ts: hottest.ts }, inverter: { value: inverter.tInverter, ts: inverter.ts },
      battery: { value: hottest.tBattery, ts: hottest.ts },
    },
  };
}

// typical share of a year's energy per month (%), as in the real app (billing.py)
const TYPICAL = { export: [2, 4, 8, 11, 13, 14, 14, 12, 9, 6, 4, 3], import: [13, 11, 9, 7, 6, 5, 5, 6, 7, 9, 10, 12] };
const PREPAYMENTS = { import: [{ from: "2026-01", eur: 58 }], export: [{ from: "2025-07", eur: 9 }, { from: "2026-03", eur: 10 }] };

/** Annual bill forecast for the calendar year, simplified from the real app (billing.py). */
/** Local date (YYYY-MM-DD) a number of days from today. */
const isoDay = (offset: number) => { const d = new Date(Date.now() + offset * 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function billing() {
  const today = new Date();
  const start = new Date(today.getFullYear(), 0, 1);
  const days = (Date.now() - start.getTime()) / 86_400_000;
  const e = energyOf(stepsBetween(start.getTime() / 1000, now()));
  const passed = (kind: "import" | "export") => TYPICAL[kind].slice(0, today.getMonth()).reduce((a, v) => a + v, 0)
    + TYPICAL[kind][today.getMonth()] * (today.getDate() - 0.5) / 31;
  const year = (kind: "import" | "export") => {
    const kwh = (kind === "import" ? e.grid_import : e.grid_export) / 1000;
    const price = (kind === "import" ? PRICE_CT : FEED_IN_CT) / 100;
    const fee = kind === "import" ? (BASE_FEE_EUR_MONTH * 12) / 365 : 0;
    const soFar = kwh * price + fee * days;
    const restKwh = kwh * (100 - passed(kind)) / passed(kind);
    const projected = soFar + restKwh * price + fee * (365 - days);
    const amount = (m: number) => {
      const key = `${today.getFullYear()}-${String(m + 1).padStart(2, "0")}`;
      return PREPAYMENTS[kind].filter((p) => p.from <= key).slice(-1)[0]?.eur ?? 0;
    };
    const paid = Array.from({ length: today.getMonth() + 1 }, (_, m) => amount(m)).reduce((a, v) => a + v, 0);
    const yearly = Array.from({ length: 12 }, (_, m) => amount(m)).reduce((a, v) => a + v, 0);
    const sign = kind === "import" ? 1 : -1;
    const monthDays = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    const paidToDate = paid - amount(today.getMonth()) * (1 - (today.getDate() - 0.5) / monthDays);
    // the grid operator's meter until two days ago (#60)
    const meter = { source: "Netze BW", from: `${today.getFullYear()}-01-01`, until: isoDay(-2), kwh: Math.round(kwh * 0.99),
      deviation_percent: kind === "import" ? -1.8 : -2.4 };
    return { paid_to_date_eur: round2(paidToDate), balance_today_eur: round2(sign * (paidToDate - soFar)), missing_days: 0, meter, from: `${today.getFullYear()}-01-01`, to: `${today.getFullYear() + 1}-01-01`, months_paid: today.getMonth() + 1,
      paid_eur: paid, yearly_payments_eur: yearly, so_far_kwh: Math.round(kwh), so_far_eur: round2(soFar),
      estimated_before: null, projected_kwh: Math.round(kwh + restKwh), projected_eur: round2(projected), method: "last_year",
      balance_now_eur: round2(sign * (paid - soFar)), balance_end_eur: round2(sign * (yearly - projected)),
      fitting_payment_eur: Math.round(projected / 12), incomplete: false };
  };
  return { settings: { import: { start_month: 1, payments: PREPAYMENTS.import }, export: { start_month: 1, payments: PREPAYMENTS.export } },
    status: { import: year("import"), export: year("export") } };
}

/** Exchange price model: cheap at noon and at night, expensive in the evening (ct/kWh incl. surcharge). */
function priceAt(ts: number): number {
  const d = new Date(ts * 1000);
  const hour = d.getHours() + d.getMinutes() / 60;
  return Math.round((26 + 7 * Math.sin(((hour - 13) / 24) * 2 * Math.PI) * -1 + (hour > 17 && hour < 21 ? 6 : 0)
    - (hour > 11 && hour < 15 ? 6 : 0)) * 100) / 100;
}

function prices(q: URLSearchParams) {
  const [from, to] = bounds("day", q.get("date"));
  const entries = [];
  for (let ts = from; ts < to; ts += QUARTER) entries.push({ ts, ct: priceAt(ts), exchange_eur_mwh: null });
  return { kind: "dynamic", feed_in_ct: FEED_IN_CT, fixed_ct: null, entries };
}

function charging() {
  return {
    settings: { enabled: false, mode: "cheapest", target_soc: 80, ready_by: 6, window_start: 22, window_end: 6,
      max_price_ct: null, power_w: 3000, battery_kwh: BATTERY_WH / 1000, legal_confirmed: false },
    active: false, last_error: null, plan: { quarters: [], reason: "Laden aus dem Netz ist aus" },
  };
}

function consumers() {
  const rod = heatingRod();
  return { consumers: [{ id: "demo1", name: "Heizstab", kind: "mypv", host: "heizstab.local", port: 502, unit: 1, channel: 0,
    url_on: "", url_off: "", power_w: 3000, min_power_w: 500, min_on_min: 0, min_off_min: 0, battery_min_soc: 50,
    price_limit_ct: null, enabled: true,
    state: { on: rod.on, power_w: rod.power, since: now() - 1800, error: null, temperature_c: rod.temperature, target_c: 60,
      status: rod.on ? "heizt" : (rod.temperature ?? 0) >= 60 ? "Wasser hat Zieltemperatur" : "Bereitschaft", actual_w: rod.power } }] };
}

function evcc() {
  return { configured: true, url: "http://evcc.local:7070", error: null, updated: now(), priority: "wallbox_first",
    state: { version: "0.316.1", site_title: "Zuhause", loadpoints: [wallbox()] } };
}

/** Charging sessions of the last months, taken from the simulation. The odometer follows from the energy, as if
 * the car used 16.9 kWh per 100 km (its range in the demo). */
function evccSessions(params: URLSearchParams) {
  const limit = Number(params.get("limit") ?? 50);
  const found = [];
  let day = dayStart(new Date());
  for (let n = 0; n < 120 && found.length < limit; n++) {
    day = dayStart(new Date((day - 86400 + 7200) * 1000));
    const charging = stepsOf(day).filter((s) => s.car > 0);
    if (!charging.length) continue;
    const energy = Math.round(charging.reduce((a, s) => a + (s.car * STEP_S) / 3600, 0) / 100) / 10;
    if (energy < 0.5) continue;
    const first = charging[0], last = charging[charging.length - 1];
    found.push({ created: new Date(first.ts * 1000).toISOString(),
      finished: new Date((last.ts + STEP_S) * 1000).toISOString(),
      loadpoint: "Carport", vehicle: "e-Golf", energy_kwh: energy, duration_s: charging.length * STEP_S,
      solar_pct: 100, price: null, soc_start: first.carSoc, soc_end: last.carSoc, added_range_km: Math.round(energy / 0.169),
      cost_eur: Math.round(energy * FEED_IN_CT) / 100, grid_cost_eur: Math.round(energy * PRICE_CT) / 100,
      odometer_km: 0 });
  }
  let odometer = 23_000; // oldest first: the energy charged replaces what was driven since the last session
  for (const s of [...found].reverse()) {
    odometer += Math.round(s.energy_kwh / 0.169);
    s.odometer_km = odometer;
  }
  return { sessions: found };
}

function controlLog() {
  const t = now();
  return { entries: [
    { ts: t - 3600 * 5, action: "consumer", details: { from: { consumer: "Heizstab", on: false }, to: { consumer: "Heizstab", on: true } },
      dry_run: true, result: "nicht geschaltet (Testmodus): Überschuss 2600 W" },
    { ts: t - 86400 * 2, action: "battery_settings", details: { from: { min_soc_on_grid: 10 }, to: { min_soc_on_grid: 20 } },
      dry_run: true, result: "nicht ausgeführt (Testmodus)" },
    { ts: t - 86400 * 3, action: "control_switches", details: { from: { "control.enabled": false }, to: { "control.enabled": true } },
      dry_run: false, result: "ok" },
  ] };
}

function diagnostics() {
  const checks = [
    ["blocks", "Registerkarte und Funktionscode", "ok", "Alle Blöcke mit dem erkannten Funktionscode lesbar."],
    ["optional_38309", "Zweites Batteriemodul (38309)", "ok", "liefert nur Nullen (vermutlich nicht vorhanden)"],
    ["optional_39327", "MPPT-Details (39327)", "ok", "liefert Werte"],
    ["block_37609", "Blocklesen (37609–37632)", "ok", "Block und Einzelwerte stimmen überein."],
    ["temp_scale", "Wechselrichtertemperatur (39141)", "ok", "31,4 °C (Rohwert 314, Faktor 0,1)"],
    ["export_limit", "Einspeisebegrenzung (46616)", "ok", "5880 W"],
    ["remote", "Fernsteuerung (46001)", "ok", "aus – kein Gerät steuert den Speicher gerade von außen"],
    ["connections", "Gleichzeitige Verbindungen", "skipped", "Nicht ausgeführt (kann andere Geräte kurz stören)."],
    ["daily_reset", "Tageszähler-Rücksetzung", "ok", "Beobachtet um 00:00 Uhr"],
    ["night", "Verbindungsabbrüche und Zähler", "ok", "0 Abbrüche, 0 Zählerauffälligkeiten gespeichert"],
  ].map(([id, title, s, summary]) => ({ id, title, status: s, summary, details: {} }));
  return { running: false, report: { created: now() - 3600, checks }, markdown: "Beispielbericht aus der Demo" };
}

const ROUTES: Record<string, (q: URLSearchParams) => unknown> = {
  "/api/status": status,
  "/api/auth/status": () => ({ configured: true, authenticated: true }),
  "/api/settings": () => settings,
  "/api/energy/summary": energySummary,
  "/api/energy/timeline": energyTimeline,
  "/api/power/timeline": powerTimeline,
  "/api/pv/inputs": pvInputs,
  "/api/temperatures/timeline": temperatures,
  "/api/battery/health": batteryHealth,
  // two invented power cuts: a short one in the evening and one at night that emptied the battery
  "/api/outages": () => {
    const d = dayStart(new Date());
    const outages = [
      { start: d - 9 * 86400 + 19.5 * 3600, end: d - 9 * 86400 + 20.2 * 3600, duration_s: 2520, soc_start: 78, soc_end: 66,
        soc_min: 66, load_kwh: 1.4, solar_kwh: 0.2, battery_kwh: 1.2, dark_since: null },
      { start: d - 23 * 86400 + 1 * 3600, end: d - 23 * 86400 + 7.5 * 3600, duration_s: 23400, soc_start: 31, soc_end: 12,
        soc_min: 10, load_kwh: 2.6, solar_kwh: 0.4, battery_kwh: 2.2, dark_since: d - 23 * 86400 + 5.2 * 3600 },
    ];
    return { current: null, outages, count: 2, total_s: 25920 };
  },
  "/api/storage": () => ({ db_bytes: 84_000_000, free_bytes: 21_500_000_000, samples: 259_200, first_sample: now() - 30 * 86400,
    bytes_per_year: 410_000_000, retention_days: 30 }),
  // the demo is the website: it is updated with every release and has nothing to install
  "/api/update": () => ({ current: "Demo", available: false, updater: false, requested: false, check: false, auto: false,
    latest: null, status: null, checked: null, error: null }),
  // shows how it looks once set up; setting it up needs a real installation
  "/api/remote": () => ({ available: true, state: "connected", port: 8080, login_url: null,
    address: "http://openampere.tail-demo.ts.net:8080", name: "openampere.tail-demo.ts.net", ip: "100.64.0.7",
    account: "demo@example.org" }),
  "/api/billing": billing,
  "/api/gridmeter": () => ({ providers: [{ key: "netze_bw", label: "Netze BW", portal: "meine.netze-bw.de", region: "Baden-Württemberg" }],
    configured: true, meters: [{ id: "demo-bezug", name: "Smart Meter · Bezug", kinds: ["import"] },
      { id: "demo-einspeisung", name: "Smart Meter · Einspeisung", kinds: ["export"] }], active: ["demo-bezug", "demo-einspeisung"],
    synced: now() - 2 * 3600, until: isoDay(-2), error: null, busy: false }),
  "/api/battery/settings": () => ({ work_mode: "self_use", min_soc: 10, max_soc: 100, min_soc_on_grid: 20, unreadable: [], external_change: null }),
  "/api/grid/export-limit": () => ({ supported: true, limit_w: 5880, rated_power_w: RATED_W, rule: "limit_60", installed_kwp: KWP,
    legal_max_w: 5880, external_change: null }),
  "/api/control/log": controlLog,
  "/api/import/cloud": () => ({ status: "done", phase: "soc", days_total: 487, work_done: 487, soc_done: 487, imported: 46752, key_set: true }),
  "/api/tokens": () => {
    const now = Date.now() / 1000;
    return { tokens: [{ id: "demo", name: "Home Assistant", scope: "read", created: now - 86400 * 12, last_used: now - 40, live_since: now - 3 * 3600 }], pairing: [],
      tls: { port: 8443, fingerprint: "3f9a0c6be1d24857a6c0f1e93b7d5a2c84e6f0b19d3c7a5e2f8b4d6c0a1e9f37", error: null } };
  },
  "/api/tariffs": () => ({ tariffs: [
    { valid_from: "2025-01-01", kind: "fixed", price_ct: PRICE_CT, surcharge_ct: 20, vat_percent: 19, feed_in_ct: FEED_IN_CT, area: "DE",
      base_fee_eur_month: BASE_FEE_EUR_MONTH },
    { valid_from: "2026-04-01", kind: "dynamic", price_ct: PRICE_CT, surcharge_ct: 20, vat_percent: 19, feed_in_ct: FEED_IN_CT, area: "DE",
      base_fee_eur_month: BASE_FEE_EUR_MONTH },
  ], eeg: { auto: true, full: false, commissioning_date: COMMISSIONED, installed_kwp: KWP, error: null,
    rate: { ct: FEED_IN_CT, period_from: "2024-02-01", period_to: "2024-07-31", full: false, funding_until: "2044-12-31",
      zones: [{ from_kw: 0, to_kw: 10, kw: KWP, share: 1, ct: FEED_IN_CT }] } } }),
  "/api/prices": prices,
  "/api/charging": charging,
  "/api/consumers": consumers,
  "/api/diagnostics": diagnostics,
  "/api/evcc": evcc,
  "/api/devices": devices,
  "/api/surplus-order": () => ({ order: ["battery", "wallbox", "c:demo1"], battery_soc: 50, items: [
    { key: "battery", name: "Speicher", kind: "battery" }, { key: "wallbox", name: "Carport", kind: "wallbox" },
    { key: "c:demo1", name: "Heizstab", kind: "heating_rod" }] }),
  "/api/devices/energy": devicesEnergy,
  "/api/devices/power": devicesPower,
  "/api/evcc/sessions": evccSessions,
  "/api/live": () => snapshot(),
};

/** Endpoints that only change something: the demo refuses them with DEMO_WRITE_MESSAGE. */
export const DEMO_WRITE_ONLY = [
  "/api/auth/login", "/api/auth/logout", "/api/auth/password", "/api/auth/setup", "/api/consumers/", "/api/evcc/loadpoints/",
  "/api/gridmeter/sync", "/api/import/cloud/file", "/api/import/cloud/start", "/api/import/cloud/stop", "/api/notify/test", "/api/outages/",
  "/api/setup/scan", "/api/setup/test", "/api/tokens/", "/api/tokens/pairing/",
];

/** Endpoints the demo never reaches: setup (the demo is already set up) and downloads (hidden in the demo). */
export const DEMO_NOT_NEEDED = ["/api/backup", "/api/backup/link", "/api/export/csv", "/api/setup/drivers", "/api/setup/networks",
  "/api/evcc/site"]; // the last one is read by evcc, not by the app

/** Every endpoint the app reads needs an answer here. `npm run check:demo` (CI) fails otherwise. */
export const DEMO_ROUTES = Object.keys(ROUTES);

export async function demoRequest<T>(method: string, path: string): Promise<T> {
  await new Promise((r) => setTimeout(r, 120)); // feels like a real server
  if (method !== "GET") throw new Error(DEMO_WRITE_MESSAGE);
  const url = new URL(path, "http://demo");
  const handler = ROUTES[url.pathname];
  if (!handler) throw new Error("In der Demo nicht verfügbar.");
  return handler(url.searchParams) as T;
}
