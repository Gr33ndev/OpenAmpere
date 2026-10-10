// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** The moment (seconds) of a wall-clock time on a day (YYYY-MM-DD) in a time zone (undefined: the device's).
 *  No imports, so the property tests (ui-tests/properties.spec.ts) can run it without the app. */
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
