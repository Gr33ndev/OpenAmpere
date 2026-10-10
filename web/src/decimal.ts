// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** Decimal numbers in input fields: shown with a comma, accepted with a comma or a point.
 *  No imports, so the property tests (ui-tests/properties.spec.ts) can run them without the app. */
export const decimalInput = (v: number, comma = true) => (comma ? String(v).replace(".", ",") : String(v));
export const parseDecimal = (v: string) => (v.trim() === "" ? Number.NaN : Number(v.replace(",", ".")));
/** Whole watts as typed: "5000", "5.000" or "5,000" (with thousands separators) or "4999,6". */
export const parseWatts = (v: string) => {
  const text = v.replace(/\s/g, "");
  return Math.round(parseDecimal(/^\d{1,3}([.,'’]\d{3})+$/.test(text) ? text.replace(/[.,'’]/g, "") : text));
};
