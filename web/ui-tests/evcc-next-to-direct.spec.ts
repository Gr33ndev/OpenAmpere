// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { WallboxesView } from "../src/NativeWallbox";
import type { EvccView } from "../src/WallboxPage";
import { expect, layoutProblems, open, test } from "./fixtures";

const SPONSOR_ERROR = "evcc startet die Wallbox nicht, weil ein gültiges Sponsortoken fehlt.";

async function evccWithout(patch: Partial<EvccView>) {
  const view = await demoRequest<EvccView>("GET", "/api/evcc");
  return { ...view, state: { ...view.state!, loadpoints: [] }, ...patch };
}

// #535: evcc stayed connected without a charge point next to a wallbox OpenAmpere reads directly, and nothing said
// that both may talk to the same wallbox
test("evcc without a charge point next to a direct wallbox gets a warning", async ({ page, api }) => {
  api.override("/api/evcc", await evccWithout({}));
  await open(page, "devices");
  await expect(page.locator(".notice", { hasText: "aber evcc hat keinen Ladepunkt" })).toBeVisible();
  await open(page, "more/wallbox");
  await expect(page.getByRole("button", { name: "direkt", exact: true })).toHaveAttribute("aria-pressed", "true");
  const notice = page.locator(".notice", { hasText: "aber evcc hat keinen Ladepunkt" });
  await expect(notice).toContainText("leere unter „über evcc“ das Feld „Adresse von evcc“");
  expect(await layoutProblems(page)).toEqual([]);
});

test("a missing sponsor token next to a direct wallbox says to remove it from evcc", async ({ page, api }) => {
  api.override("/api/evcc", await evccWithout({ error: SPONSOR_ERROR, error_code: "sponsor" }));
  await open(page, "devices");
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toBeVisible();
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toHaveCount(0);
  await open(page, "more/wallbox");
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toBeVisible();
  await page.getByRole("button", { name: "über evcc" }).click();
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toBeVisible();
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toHaveCount(0);
});

// review of #536: an expired token for evcc's own wallbox is what evcc needs, so its advice must stay
test("with charge points of its own evcc's sponsor error stays next to a direct wallbox", async ({ page, api }) => {
  const view = await demoRequest<EvccView>("GET", "/api/evcc");
  api.override("/api/evcc", { ...view, error: SPONSOR_ERROR, error_code: "sponsor" });
  await open(page, "devices");
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toHaveCount(0);
});

// review of #536: OpenAmpere does not connect to a wallbox that is switched off
test("a switched-off direct wallbox gets no warning about evcc", async ({ page, api }) => {
  const native = await demoRequest<WallboxesView>("GET", "/api/wallboxes");
  api.override("/api/wallboxes", { ...native, wallboxes: native.wallboxes.map((w) => ({ ...w, enabled: false })) });
  api.override("/api/evcc", await evccWithout({ error: SPONSOR_ERROR, error_code: "sponsor" }));
  await open(page, "devices");
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toHaveCount(0);
  await open(page, "more/wallbox");  // opens on "direkt": the wallbox is set up that way
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toHaveCount(0);
  await page.getByRole("button", { name: "über evcc" }).click();
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toHaveCount(0);
});

test("without a direct wallbox evcc's sponsor error stays as it is", async ({ page, api }) => {
  const native = await demoRequest<WallboxesView>("GET", "/api/wallboxes");
  api.override("/api/wallboxes", { ...native, wallboxes: [] });
  api.override("/api/evcc", await evccWithout({ error: SPONSOR_ERROR, error_code: "sponsor" }));
  await open(page, "more/wallbox");
  await expect(page.locator(".notice", { hasText: SPONSOR_ERROR })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "brauchst du kein Token" })).toHaveCount(0);
});

test("evcc with its own charge point next to a direct wallbox only gets a hint", async ({ page }) => {
  await open(page, "devices");
  await expect(page.locator(".section-title", { hasText: "Wallbox" })).toBeVisible();
  await expect(page.locator(".notice", { hasText: "evcc" })).toHaveCount(0);
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await expect(page.getByText("Das passt, wenn evcc eine andere Wallbox steuert.")).toBeVisible();
});
