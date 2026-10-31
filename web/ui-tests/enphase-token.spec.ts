// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, open, test } from "./fixtures";

// #538: my system says how long the Enphase access token is valid and warns before it runs out
async function withToken(api: { override(path: string, body: unknown): void }, days: number | null) {
  const status = (await demoRequest("GET", "/api/status")) as Record<string, unknown>;
  api.override("/api/status", { ...status, token_days_left: days });
}

test("my system shows the days left of the Enphase token and warns 30 days before", async ({ page, api }) => {
  await withToken(api, 12);
  await open(page, "more/installation");
  await expect(page.getByText("Noch 12 Tage gültig")).toBeVisible();
  await expect(page.locator(".notice.warn", { hasText: "Enphase-Konto" })).toBeVisible();
  await page.getByRole("button", { name: "Zur Verbindung" }).click();
  await expect(page.getByPlaceholder(/192\.168\.178\.50/)).toBeVisible();  // the connection form with the token field
});

test("a token that ran out is an error, one with time left no warning", async ({ page, api }) => {
  await withToken(api, -2);
  await open(page, "more/installation");
  await expect(page.getByText("Abgelaufen", { exact: true })).toBeVisible();
  await expect(page.locator(".notice.error", { hasText: "ist abgelaufen" })).toBeVisible();
  await withToken(api, 200);
  await page.reload();
  await expect(page.getByText("Noch 200 Tage gültig")).toBeVisible();
  await expect(page.locator(".notice", { hasText: "Enphase-Konto" })).toHaveCount(0);
});

test("no token, no row", async ({ page }) => {
  await open(page, "more/installation");
  await expect(page.getByText("Zugangs-Token (Enphase)")).toHaveCount(0);
});
