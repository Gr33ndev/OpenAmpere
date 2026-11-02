// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #567: another device at the inverter's address (a tablet in another network, or a replaced inverter)
test("overview says another device answers and asks before taking it over", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, connected: false,
    wrong_device: { since: Date.now() / 1000 - 600, found: "60BH…02", expected: "60BH…01" } });
  await open(page, "");
  const notice = page.locator(".notice", { hasText: "anderes Gerät" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("60BH…02");
  await expect(notice).toContainText("60BH…01");
  expect(await layoutProblems(page)).toEqual([]);

  let posted = false;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/setup/accept-device")) posted = true;
  });
  await notice.getByRole("button", { name: "Das ist mein neuer Wechselrichter" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("wirklich getauscht");
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  expect(posted).toBe(false);
  await notice.getByRole("button", { name: "Das ist mein neuer Wechselrichter" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => posted).toBe(true);
});

test("no notice while the own inverter answers", async ({ page }) => {
  await open(page, "");
  await expect(page.locator(".page-head")).toBeVisible();
  await expect(page.locator(".notice", { hasText: "anderes Gerät" })).toHaveCount(0);
});
