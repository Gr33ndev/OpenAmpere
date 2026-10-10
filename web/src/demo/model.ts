// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
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
  household: number; // load without the devices below
  car: number; carConnected: boolean; carSoc: number; // wallbox (evcc in the real app)
  rod: number; water: number; // my-PV heating rod and hot water temperature
};

export const CAR_KWH = 44;
const CAR_MIN_W = 1380; // 6 A, one phase
const ROD_MAX_W = 3000;
const WATER_TARGET = 60;
export const DEVICES_FROM_SOC = 50; // the devices get surplus once the battery has this state of charge

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
  return Math.sin(Math.PI * x) ** 1.4 * (0.3 + 0.7 * (season + 1) / 2);
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
  const clouds = (0.2 + 0.8 * rnd() ** 0.6) * (0.55 + 0.45 * (season + 1) / 2); // more grey days in winter
  const washer = rnd() < 0.45 ? 10 + rnd() * 5 : null;
  const weekend = date.getDay() === 0 || date.getDay() === 6;
  const steps: Step[] = [];
  let cloud = clouds;
  // the car is at home on weekends and on most weekdays (home office), plugged in from the morning
  const carHome = weekend || rnd() < 0.6;
  const carFrom = 8.5 + rnd() * 2, carUntil = 16.5 + rnd() * 2.5;
  let carSoc = 20 + rnd() * 30;
  let water = 42 + rnd() * 6;
  for (let i = 0; i < 86400 / STEP_S; i++) {
    const ts = start + i * STEP_S;
    const hour = (i * STEP_S) / 3600;
    const h = STEP_S / 3600;
    cloud = Math.min(1, Math.max(0.1, cloud + (rnd() - 0.5) * 0.25 * (1 - clouds) + (clouds - cloud) * 0.1));
    const pv1 = Math.round(KWP * 1000 * 0.6 * 0.85 * sun(hour, doy) * cloud);
    const pv2 = Math.round(KWP * 1000 * 0.4 * 0.85 * sun(hour - 1.3, doy) * cloud);
    const pv = pv1 + pv2;
    const household = Math.round(load(hour, weekend, rnd, washer));
    // shared like the real control: battery first up to 80 %, then the car (wallbox first), then the heating rod
    let free = pv - household;
    let battery = 0; // + = discharging
    const room = ((100 - soc) / 100) * BATTERY_WH / h;
    if (free > 0 && soc < DEVICES_FROM_SOC) {
      battery = -Math.min(free, MAX_BATTERY_W, room);
      free += battery;
    }
    const carConnected = carHome && hour >= carFrom && hour < carUntil;
    let car = 0;
    if (carConnected && carSoc < 80 && free >= CAR_MIN_W) {
      car = Math.min(11_000, Math.floor(free / 230) * 230);
      free -= car;
      carSoc = Math.min(80, carSoc + (car * h / 1000 / CAR_KWH) * 100);
    }
    let rod = 0;
    if (water < WATER_TARGET && free >= 600) {
      rod = Math.min(ROD_MAX_W, Math.round((free - 100) / 50) * 50);
      free -= rod;
    }
    // showers in the morning and in the evening use hot water, the rest is standing loss
    const tapping = (hour >= 6.5 && hour < 7.5) || (hour >= 19 && hour < 20.5) ? 6 : 0.25;
    water = Math.max(35, water + (rod * h / 1000) * 4.3 - tapping * h);
    if (free > 0 && soc < 100 && battery === 0) battery = -Math.min(free, MAX_BATTERY_W, room);
    else if (free > 0 && soc < 100) battery -= Math.min(free, MAX_BATTERY_W + battery, room + battery);
    const deficit = household + car + rod - pv;
    if (deficit > 0 && soc > 10) battery = Math.min(deficit, MAX_BATTERY_W, ((soc - 10) / 100) * BATTERY_WH / h);
    soc = Math.min(100, Math.max(10, soc - (battery * h / BATTERY_WH) * 100 * (battery < 0 ? 0.95 : 1)));
    const loadW = household + car + rod;
    const grid = Math.round(loadW - pv - battery);
    const ambient = 12 + 8 * Math.sin((2 * Math.PI * (doy - 110)) / 365) + 4 * Math.sin((Math.PI * (hour - 8)) / 12);
    steps.push({ ts, pv, pv1, pv2, load: loadW, grid, battery: Math.round(battery), soc: Math.round(soc * 10) / 10,
      tInverter: Math.round((ambient + 8 + pv / 400) * 10) / 10, tBattery: Math.round((ambient + 6 + Math.abs(battery) / 800) * 10) / 10,
      household, car, carConnected, carSoc: Math.round(carSoc), rod, water: Math.round(water * 10) / 10 });
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
