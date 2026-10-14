// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

async function statusWith(overwritten: Status["overwritten"]) {
  return { ...(await demoRequest<Status>("GET", "/api/status")), overwritten };
}

// the old smartbox sets changes back: the app explains it on every page instead of only on the battery page
test("a setting another device set back is explained once", async ({ page, api }) => {
  const ts = Date.now() / 1000 - 120;
  api.override("/api/status", await statusWith({ ts, settings: ["min_soc_on_grid", "work_mode"] }));
  await open(page, "report");
  const dialog = page.getByRole("dialog", { name: "Deine Änderung wurde zurückgesetzt" });
  await expect(dialog).toContainText("Notstrom-Reserve und Betriebsmodus");
  await expect(dialog).toContainText("Smartbox");
  // the dialog is taller than a phone screen: it starts at its title and scrolls inside, not cut off at the top
  const title = (await dialog.getByRole("heading").boundingBox())!;
  expect(title.y).toBeGreaterThanOrEqual(0);
  await expect(dialog.getByRole("link", { name: "Anleitung" })).toHaveAttribute("href", /README\.de\.md#die-bisherige-smartbox/);
  expect(await layoutProblems(page)).toEqual([]);

  await dialog.getByRole("button", { name: "Verstanden" }).click();
  await expect(dialog).toBeHidden();
  await page.reload();
  await expect(page.locator("nav.bottom")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // a new case is shown again
  api.override("/api/status", await statusWith({ ts: ts + 60, settings: ["export_limit_w"] }));
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Einspeisebegrenzung");
});

test("closing without confirming shows it again next time", async ({ page, api }) => {
  api.override("/api/status", await statusWith({ ts: Date.now() / 1000 - 60, settings: ["max_soc"] }));
  await open(page, "");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Schließen" }).click();
  await expect(dialog).toBeHidden();
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Ladegrenze");
});

test("no dialog while nothing was set back", async ({ page }) => {
  await open(page, "");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
