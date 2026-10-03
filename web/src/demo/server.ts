/** Answers the app's API requests inside the browser for the public demo. Nothing leaves the browser,
 *  nothing is saved: every change is refused with a friendly message. */

import { BATTERY_WH, dayStart, energyOf, firstDayStart, KWP, stepsBetween, stepsOf, STEP_S, type Energy, type Step } from "./model";

export const DEMO_WRITE_MESSAGE = "Das ist nur die Demo, hier lässt sich nichts ändern. Für deine eigene Anlage installierst du OpenAmpere.";

const RATED_W = 10_000;
const PRICE_CT = 35;
const FEED_IN_CT = 8;
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

function money(e: Energy) {
  return {
    savings_eur: Math.round((Math.max(0, e.load - e.grid_import) * PRICE_CT + e.grid_export * FEED_IN_CT) / 1000) / 100,
    feed_in_eur: Math.round((e.grid_export * FEED_IN_CT) / 1000) / 100,
    grid_cost_eur: Math.round((e.grid_import * PRICE_CT) / 1000) / 100,
    incomplete: false,
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

export function snapshot() {
  const s = current();
  if (!s) return null;
  const wobble = (v: number, f = 0.04) => Math.round(v * (1 + (Math.random() - 0.5) * f));
  const pv1 = wobble(s.pv1), pv2 = wobble(s.pv2), house = wobble(s.load, 0.06);
  const pv = pv1 + pv2;
  const battery = s.battery;
  if (!totalsCache || now() - totalsCache.at > 300) totalsCache = { at: now(), value: totals() };
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
    totals: totalsCache.value,
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
    plan_active: false, plan_time: null, plan_soc: null,
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
      active: car.charging, on: car.charging, temperature_c: null, target_c: null, status: null, error: null, override: null },
    { key: "c:demo1", id: "demo1", source: "openampere", name: "Heizstab", kind: "heating_rod", enabled: true,
      power_w: rod.power, active: rod.on, on: rod.on, temperature_c: rod.temperature, target_c: 60,
      status: rod.on ? "heizt" : (rod.temperature ?? 0) >= 60 ? "Wasser hat Zieltemperatur" : "Bereitschaft",
      error: null, override: null },
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
    timezone: "Europe/Berlin", "pv.input_names": INPUT_NAMES, "pv.installed_kwp": KWP, "grid.feed_in_rule": "limit_60",
    "notify.ntfy_url": "", "notify.on_unreachable": true, "notify.on_alarm": true, "notify.on_overwritten": true,
    "notify.on_battery_full": false, "notify.on_cheap_power": false,
    "evcc.url": "http://evcc.local:7070", "evcc.priority": "wallbox_first",
  },
  secrets: { "cloud.api_key": { set: false, hint: null }, "notify.ntfy_token": { set: false, hint: null },
    "evcc.password": { set: false, hint: null } },
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
  };
}

function energySummary(q: URLSearchParams) {
  const period = q.get("period") ?? "day";
  const [from, to] = bounds(period, q.get("date"));
  const steps = stepsBetween(from, to);
  const e = energyOf(steps);
  return { period, from, to, quarters: Math.ceil(steps.length / 3), partial_since: null, energy_wh: e, ...ratios(e), money: money(e) };
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
  return { entries: stepsBetween(from, to).map((s) => ({ ts: s.ts, inverter: s.tInverter, battery: s.tBattery })) };
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

/** Charging sessions of the last weeks, taken from the simulation. */
function evccSessions() {
  const sessions = [];
  let day = dayStart(new Date());
  for (let n = 0; n < 45 && sessions.length < 12; n++) {
    day = dayStart(new Date((day - 86400 + 7200) * 1000));
    const charging = stepsOf(day).filter((s) => s.car > 0);
    if (!charging.length) continue;
    const energy = charging.reduce((a, s) => a + (s.car * STEP_S) / 3600, 0) / 1000;
    if (energy < 0.5) continue;
    sessions.push({ created: new Date(charging[0].ts * 1000).toISOString(),
      finished: new Date((charging[charging.length - 1].ts + STEP_S) * 1000).toISOString(),
      loadpoint: "Carport", vehicle: "e-Golf", energy_kwh: Math.round(energy * 10) / 10,
      duration_s: charging.length * STEP_S, solar_pct: 100, price: null });
  }
  return { sessions };
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
    ["block_37609", "Blocklesen 37609–37632", "ok", "Block und Einzelwerte stimmen überein."],
    ["temp_scale", "Wechselrichtertemperatur 39141", "ok", "Rohwert 31 → Faktor vermutlich 1"],
    ["export_limit", "Einspeisebegrenzung 46616", "ok", "5880 W"],
    ["remote", "Fernsteuerung aktiv?", "ok", "nicht aktiv"],
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
  "/api/battery/settings": () => ({ work_mode: "self_use", min_soc: 10, max_soc: 100, min_soc_on_grid: 20, unreadable: [], external_change: null }),
  "/api/grid/export-limit": () => ({ supported: true, limit_w: 5880, rated_power_w: RATED_W, rule: "limit_60", installed_kwp: KWP,
    legal_max_w: 5880, external_change: null }),
  "/api/control/log": controlLog,
  "/api/import/cloud": () => ({ status: "done", phase: "soc", days_total: 487, work_done: 487, soc_done: 487, imported: 46752, key_set: true }),
  "/api/tariffs": () => ({ tariffs: [
    { valid_from: "2025-01-01", kind: "fixed", price_ct: PRICE_CT, surcharge_ct: 20, vat_percent: 19, feed_in_ct: FEED_IN_CT, area: "DE" },
    { valid_from: "2026-04-01", kind: "dynamic", price_ct: PRICE_CT, surcharge_ct: 20, vat_percent: 19, feed_in_ct: FEED_IN_CT, area: "DE" },
  ] }),
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
  "/api/import/cloud/file", "/api/import/cloud/start", "/api/import/cloud/stop", "/api/notify/test",
  "/api/setup/scan", "/api/setup/test",
];

/** Endpoints the demo never reaches: setup (the demo is already set up) and downloads (hidden in the demo). */
export const DEMO_NOT_NEEDED = ["/api/backup", "/api/export/csv", "/api/setup/drivers", "/api/setup/networks",
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
