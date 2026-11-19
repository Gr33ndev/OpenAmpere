// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

// #654: the exchange prices also on the page of grid charging, with tomorrow once it is known
test("grid charging at the cheapest time shows today's and tomorrow's prices", async ({ page }) => {
  await open(page, "devices/charging");
  await expect(page.getByText("Strompreis heute", { exact: true })).toBeVisible();
  await expect(page.getByText("Börsenpreise zuletzt abgerufen", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Morgen" }).click();
  await expect(page.getByText("Strompreis morgen", { exact: true })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  // a fixed time window does not depend on the prices
  await page.getByRole("button", { name: "Festes Zeitfenster" }).click();
  await expect(page.getByText("Strompreis morgen", { exact: true })).toHaveCount(0);
});

// #654: a failed fetch (no internet, a wrong VPN) is told in the app, not only in the log
test("prices that cannot be fetched are explained", async ({ page, api }) => {
  const failed = Math.floor(Date.now() / 1000) - 7200;
  api.override("/api/prices", { kind: "dynamic", feed_in_ct: 8, fixed_ct: null, entries: [], next_day: false,
    fetch: { last_ok: failed - 3600, last_error: failed + 600, failing_since: failed, error: "timed out" } });
  await open(page, "more/tariff");
  await expect(page.getByText("OpenAmpere erreicht den Börsenpreis-Dienst (aWATTar) nicht", { exact: false })).toBeVisible();
  await expect(page.getByText("Meldung: timed out")).toBeVisible();
  await expect(page.getByRole("button", { name: "Morgen" })).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);
});
