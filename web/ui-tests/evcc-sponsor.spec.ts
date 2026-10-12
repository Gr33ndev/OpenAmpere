// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { EvccSponsor, EvccView } from "../src/WallboxPage";
import { expect, layoutProblems, open, test } from "./fixtures";

async function evccWith(sponsor: EvccSponsor) {
  const view = await demoRequest<EvccView>("GET", "/api/evcc");
  return { ...view, state: { ...view.state, sponsor } };
}

// #317: the evcc test token runs out after days and the wallbox stops charging without a warning
test("devices page warns while evcc runs with the test token", async ({ page, api }) => {
  api.override("/api/evcc", await evccWith(
    { active: true, trial: true, expires_at: "2030-10-17T00:00:00Z", expires_soon: true }));
  await open(page, "devices");
  const notice = page.locator(".notice", { hasText: "Testtoken" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("17. Oktober");
  expect(await layoutProblems(page)).toEqual([]);
});

test("wallbox page says when the sponsor token expires soon", async ({ page, api }) => {
  api.override("/api/evcc", await evccWith(
    { active: true, trial: false, expires_at: "2030-11-02T00:00:00Z", expires_soon: true }));
  await open(page, "more/wallbox");
  await expect(page.locator(".notice", { hasText: "Sponsortoken von evcc läuft am" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Wallbox-Liste von evcc" })).toBeVisible();
});

test("no sponsor warning with a token far from expiring", async ({ page }) => {
  await open(page, "devices");
  await expect(page.locator(".section-title", { hasText: "Wallbox" })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "Sponsortoken" })).toHaveCount(0);
  await expect(page.locator(".notice", { hasText: "Testtoken" })).toHaveCount(0);
});
