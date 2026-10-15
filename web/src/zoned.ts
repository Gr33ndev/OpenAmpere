// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** The calendar day after an ISO date: not +24 h, a day has 23 or 25 hours at the clock change (#244). */
export function nextDay(day: string): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

/** The moment (seconds) of a wall-clock time on a day (YYYY-MM-DD) in a time zone (undefined: the device's).
 *  This file has no imports, so the property tests (ui-tests/properties.spec.ts) can run it without the app. */
export function zonedTime(day: string, hour: number, minute: number, timeZone?: string): number {
  const [y, mo, d] = day.split("-").map(Number);
  const wall = Date.UTC(y, mo - 1, d, hour, minute) / 1000;
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, hourCycle: "h23", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const offset = (ts: number) => {
    const p = Object.fromEntries(parts.formatToParts(new Date(ts * 1000)).map((x) => [x.type, Number(x.value)]));
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000 - ts;
  };
  return wall - offset(wall - offset(wall)); // twice: right also next to a daylight saving change
}

/** Start (seconds) of every bucket of a period in a time zone, like bucket_start on the server: the days of a week or a
 *  month, the months of a year. A chart gets the whole period on its axis, also where there is no data yet (#403).
 *  first: the period's first day (YYYY-MM-DD). */
export function periodBuckets(first: string, period: "week" | "month" | "year", timeZone?: string): number[] {
  if (period === "year") {
    return Array.from({ length: 12 }, (_, m) => zonedTime(`${first.slice(0, 4)}-${String(m + 1).padStart(2, "0")}-01`, 0, 0, timeZone));
  }
  const days = [first];
  for (let day = nextDay(first); period === "week" ? days.length < 7 : day.slice(0, 7) === first.slice(0, 7); day = nextDay(day)) {
    days.push(day);
  }
  return days.map((day) => zonedTime(day, 0, 0, timeZone));
}
