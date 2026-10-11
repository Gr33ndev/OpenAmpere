// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** Cells a spreadsheet would run as a formula, also inside quotes (e.g. the name of an app in the control log). */
export const FORMULA = /^[=+\-@\t\r]/;

/** CSV for Excel or LibreOffice: every cell quoted, rows ending with CRLF, with a BOM so the umlauts survive.
 *  Cells that would be a formula start with ' (shown as text, GHSA-9qfr-fvc7-hm9g).
 *  No imports, so the property tests (ui-tests/properties.spec.ts) can run it without the app. */
export function csv(rows: string[][], separator: string): string {
  const cell = (v: string) => `"${(FORMULA.test(v) ? `'${v}` : v).replace(/"/g, '""')}"`;
  return `﻿${rows.map((row) => row.map(cell).join(separator)).join("\r\n")}\r\n`;
}
