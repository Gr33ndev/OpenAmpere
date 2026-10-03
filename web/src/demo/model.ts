/** Simulated energy system for the public demo: a 9.8 kWp roof (south + west), a 10 kWh battery and a
 *  household. Deterministic per day (seeded), so the history looks the same on every visit. */

export const KWP = 9.8;
export const BATTERY_WH = 10_000;
const MAX_BATTERY_W = 5000;
export const STEP_S = 300;
const HISTORY_DAYS = 420;

export type Step = {
  ts: number; pv: number; pv1: number; pv2: number; load: number; grid: number; battery: number; soc: number;
  tInverter: number; tBattery: number;
};

function random(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function dayStart(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000;
}

function dayOfYear(ts: number): number {
  const d = new Date(ts * 1000);
  return (dayStart(d) - new Date(d.getFullYear(), 0, 1).getTime() / 1000) / 86400;
}

/** Fraction of the peak the modules deliver at a local hour (0 at night). */
function sun(hour: number, doy: number): number {
  const season = Math.sin((2 * Math.PI * (doy - 80)) / 365);
  const length = 12 + 4.2 * season;
  const x = (hour - (13.3 - length / 2)) / length;
  if (x <= 0 || x >= 1) return 0;
  // winter sun is much weaker (low angle, more haze); calibrated to about 900 kWh per kWp and year
  return Math.pow(Math.sin(Math.PI * x), 1.4) * (0.3 + 0.7 * (season + 1) / 2);
}

function load(hour: number, weekend: boolean, rnd: () => number, washer: number | null): number {
  let w = 280 + rnd() * 120;
  if (hour >= 6.5 && hour < 8) w += weekend ? 200 : 700;
  if (hour >= 11.5 && hour < 13.5) w += weekend ? 1300 : 300;
  if (hour >= 17.5 && hour < 21.5) w += 900;
  if (hour >= 21.5 && hour < 23) w += 400;
  if (washer !== null && hour >= washer && hour < washer + 1.5) w += 1800;
  if (rnd() < 0.04) w += 1500 + rnd() * 1500; // kettle, oven, ...
  return w;
}

function simulateDay(start: number, soc: number): Step[] {
  const doy = dayOfYear(start + 43200);
  const date = new Date(start * 1000);
  const rnd = random(start / 86400);
  const season = Math.sin((2 * Math.PI * (doy - 80)) / 365);
  const clouds = (0.2 + 0.8 * Math.pow(rnd(), 0.6)) * (0.55 + 0.45 * (season + 1) / 2); // more grey days in winter
  const washer = rnd() < 0.45 ? 10 + rnd() * 5 : null;
  const weekend = date.getDay() === 0 || date.getDay() === 6;
  const steps: Step[] = [];
  let cloud = clouds;
  for (let i = 0; i < 86400 / STEP_S; i++) {
    const ts = start + i * STEP_S;
    const hour = (i * STEP_S) / 3600;
    cloud = Math.min(1, Math.max(0.1, cloud + (rnd() - 0.5) * 0.25 * (1 - clouds) + (clouds - cloud) * 0.1));
    const pv1 = Math.round(KWP * 1000 * 0.6 * 0.85 * sun(hour, doy) * cloud);
    const pv2 = Math.round(KWP * 1000 * 0.4 * 0.85 * sun(hour - 1.3, doy) * cloud);
    const pv = pv1 + pv2;
    const house = Math.round(load(hour, weekend, rnd, washer));
    let battery = 0; // + = discharging
    const surplus = pv - house;
    if (surplus > 0 && soc < 100) battery = -Math.min(surplus, MAX_BATTERY_W, ((100 - soc) / 100) * BATTERY_WH * 3600 / STEP_S);
    if (surplus < 0 && soc > 10) battery = Math.min(-surplus, MAX_BATTERY_W, ((soc - 10) / 100) * BATTERY_WH * 3600 / STEP_S);
    soc = Math.min(100, Math.max(10, soc - (battery * STEP_S / 3600 / BATTERY_WH) * 100 * (battery < 0 ? 0.95 : 1)));
    const grid = Math.round(house - pv - battery);
    const ambient = 12 + 8 * Math.sin((2 * Math.PI * (doy - 110)) / 365) + 4 * Math.sin((Math.PI * (hour - 8)) / 12);
    steps.push({ ts, pv, pv1, pv2, load: house, grid, battery: Math.round(battery), soc: Math.round(soc * 10) / 10,
      tInverter: Math.round((ambient + 8 + pv / 400) * 10) / 10, tBattery: Math.round((ambient + 6 + Math.abs(battery) / 800) * 10) / 10 });
  }
  return steps;
}

const days = new Map<number, Step[]>();
let firstDay = 0;

/** All steps of a local day (only up to "now" for today). */
export function stepsOf(start: number, now = Date.now() / 1000): Step[] {
  if (!firstDay) firstDay = dayStart(new Date((now - HISTORY_DAYS * 86400) * 1000));
  if (start < firstDay || start > now) return [];
  if (!days.has(start)) {
    // simulate forward from the last known day so the state of charge continues across midnight
    let day = firstDay;
    let soc = 40;
    while (day <= start) {
      const cached = days.get(day);
      const steps = cached ?? simulateDay(day, soc);
      if (!cached) days.set(day, steps);
      soc = steps[steps.length - 1].soc;
      day = dayStart(new Date((day + 86400 + 7200) * 1000)); // robust against DST
    }
  }
  return (days.get(start) ?? []).filter((s) => s.ts <= now);
}

export function firstDayStart(): number {
  stepsOf(dayStart(new Date()));
  return firstDay;
}

/** Steps between two timestamps, across days. */
export function stepsBetween(from: number, to: number, now = Date.now() / 1000): Step[] {
  const out: Step[] = [];
  let day = dayStart(new Date(from * 1000));
  while (day < to && day <= now) {
    for (const s of stepsOf(day, now)) if (s.ts >= from && s.ts < to) out.push(s);
    day = dayStart(new Date((day + 86400 + 7200) * 1000));
  }
  return out;
}

export type Energy = { pv: number; load: number; grid_import: number; grid_export: number; battery_charge: number; battery_discharge: number };

export function energyOf(steps: Step[]): Energy {
  const e: Energy = { pv: 0, load: 0, grid_import: 0, grid_export: 0, battery_charge: 0, battery_discharge: 0 };
  const h = STEP_S / 3600;
  for (const s of steps) {
    e.pv += s.pv * h;
    e.load += s.load * h;
    e.grid_import += Math.max(0, s.grid) * h;
    e.grid_export += Math.max(0, -s.grid) * h;
    e.battery_charge += Math.max(0, -s.battery) * h;
    e.battery_discharge += Math.max(0, s.battery) * h;
  }
  return e;
}
