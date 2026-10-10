/** Property-based tests (fast-check) of pure functions: rules that must hold for any input, not only for the
 *  examples in the other tests (#203). They need no browser, so they run in one project only. */
import { expect, test } from "@playwright/test";
import fc from "fast-check";
import { csv } from "../src/csv";
import { decimalInput, parseDecimal } from "../src/decimal";

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
