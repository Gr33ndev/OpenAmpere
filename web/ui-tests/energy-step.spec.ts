// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, open, test } from "./fixtures";

// #194: energy from counters that count in 0.1 kWh steps must not show a second decimal that is always 0
test("counter energy has two decimals for 0.01 kWh counters", async ({ page }) => {
  await open(page, "");
  await expect(page.locator(".tile-value").first()).toHaveText(/^\d+,\d\d kWh$/);
});

test("counter energy has one decimal for 0.1 kWh counters, device energy keeps two", async ({ page, api }) => {
  api.override("/api/status", { ...(await demoRequest<Status>("GET", "/api/status")), energy_step_wh: 100 });
  await open(page, "");
  const tiles = page.locator(".tile-value");
  await expect(tiles.first()).toHaveText(/^\d+,\d kWh$/);
  for (const value of await tiles.allTextContents()) expect(value).toMatch(/^\d+,\d kWh$/);
  const device = page.locator(".device-today", { hasText: "kWh" }).first();
  await expect(device).toContainText(/\d,\d\d kWh/);
});
