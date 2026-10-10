// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/** CSV for Excel or LibreOffice: every cell quoted, rows ending with CRLF, with a BOM so the umlauts survive.
 *  No imports, so the property tests (ui-tests/properties.spec.ts) can run it without the app. */
export function csv(rows: string[][], separator: string): string {
  const cell = (v: string) => `"${v.replace(/"/g, '""')}"`;
  return `﻿${rows.map((row) => row.map(cell).join(separator)).join("\r\n")}\r\n`;
}
