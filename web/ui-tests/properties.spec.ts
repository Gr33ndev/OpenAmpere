// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** Property-based tests (fast-check) of pure functions: rules that must hold for any input, not only for the
 *  examples in the other tests (#203). They need no browser, so they run in one project only. */
import { expect, test } from "@playwright/test";
import fc from "fast-check";
import { csv } from "../src/csv";
import { decimalInput, parseDecimal, parseWatts } from "../src/decimal";
import { zonedTime } from "../src/zoned";

test.beforeEach(() => {
  test.skip(test.info().project.name !== "desktop", "pure functions, one run is enough");
});

/** Reads CSV the way spreadsheets do (RFC 4180): quoted cells may contain separators, quotes ("") and line breaks. */
function parseCsv(text: string, separator: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let i = 0;
  while (i < text.length) {
    expect(text[i]).toBe('"'); // csv() quotes every cell
    let value = "";
    i++;
    for (;;) {
      const quote = text.indexOf('"', i);
      expect(quote).toBeGreaterThanOrEqual(0);
      value += text.slice(i, quote);
      i = quote + 1;
      if (text[i] !== '"') break;
      value += '"';
      i++;
    }
    row.push(value);
    if (text[i] === separator) {
      i++;
    } else {
      expect(text.slice(i, i + 2)).toBe("\r\n");
      i += 2;
      rows.push(row);
      row = [];
    }
  }
  return rows;
}

test("the CSV export reads back exactly, whatever the cells contain", () => {
  const cells = fc.oneof(fc.string({ unit: "binary" }), fc.constantFrom('"', ";", ",", "\r\n", "\n", '""', "ä€😀"));
  fc.assert(fc.property(
    fc.array(fc.array(cells, { minLength: 1, maxLength: 7 }), { minLength: 1, maxLength: 20 }),
    fc.constantFrom(";", ","),
    (rows, separator) => {
      const text = csv(rows, separator);
      expect(text.startsWith("﻿")).toBe(true); // BOM, so spreadsheets read the umlauts as UTF-8
      expect(parseCsv(text.slice(1), separator)).toEqual(rows);
    },
  ));
});

test("a number shown in an input field is read back as the same number", () => {
  fc.assert(fc.property(fc.double({ noNaN: true, noDefaultInfinity: true }), (value) => {
    expect(parseDecimal(decimalInput(value)) === value).toBe(true);
  }));
});

test("input with a decimal point or a decimal comma means the same number", () => {
  fc.assert(fc.property(fc.double({ noNaN: true, noDefaultInfinity: true }), (value) => {
    const text = String(value);
    expect(parseDecimal(text) === value).toBe(true);
    expect(parseDecimal(text.replace(".", ",")) === value).toBe(true);
  }));
  for (const empty of ["", " ", "\t"]) expect(parseDecimal(empty)).toBeNaN();
});

test("watts can be typed with thousands separators, like they are written in German or English", () => {
  fc.assert(fc.property(fc.integer({ min: 0, max: 99_999 }), (watts) => {
    expect(parseWatts(String(watts))).toBe(watts);
    for (const separator of [".", ",", " "]) {  // #223: "5.000" was read as 5 W
      expect(parseWatts(watts.toLocaleString("en-US").replace(/,/g, separator))).toBe(watts);
    }
  }));
  expect(parseWatts("4999,6")).toBe(5000);
});

test("a number in an input field uses the decimal separator of the app's language", () => {
  fc.assert(fc.property(fc.double({ noNaN: true, noDefaultInfinity: true }), (value) => {
    expect(parseDecimal(decimalInput(value, false)) === value).toBe(true);
    expect(decimalInput(value, false)).not.toContain(",");
  }));
});

test("a wall-clock time in the plant's time zone is that time there, also around daylight saving changes", () => {
  const days = fc.date({ min: new Date("2020-01-01T00:00:00Z"), max: new Date("2035-12-31T00:00:00Z"), noInvalidDate: true })
    .map((d) => d.toISOString().slice(0, 10));
  fc.assert(fc.property(days, fc.integer({ min: 0, max: 23 }), fc.integer({ min: 0, max: 59 }),
    fc.constantFrom("Europe/Berlin", "America/New_York", "Australia/Sydney", "UTC"), (day, hour, minute, zone) => {
      const ts = zonedTime(day, hour, minute, zone);
      const shown = new Intl.DateTimeFormat("en-CA", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit",
        day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(ts * 1000));
      const expected = `${day}, ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
      // a time that does not exist (the hour skipped in spring) comes out one hour later; every other one exactly
      const hourLater = `${day}, ${String(hour + 1).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
      expect([expected, hourLater]).toContain(shown);
      if (shown === hourLater) expect(zonedTime(day, hour + 1, minute, zone)).toBe(ts);
    }));
});
