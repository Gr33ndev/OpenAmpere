// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

async function statusWith(storage: Status["storage"]) {
  return { ...(await demoRequest<Status>("GET", "/api/status")), storage };
}

// #170: readings that cannot be stored used to be visible only in the server log
test("overview says when readings cannot be stored", async ({ page, api }) => {
  api.override("/api/status", await statusWith(
    { failing_since: Date.now() / 1000 - 3600, error: "full", free_bytes: 0, low_space: true }));
  await open(page, "");
  const notice = page.locator(".notice", { hasText: "keine Messwerte speichern" });
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("Speicherplatz");
  expect(await layoutProblems(page)).toEqual([]);
});

test("overview warns when disk space runs low", async ({ page, api }) => {
  api.override("/api/status", await statusWith(
    { failing_since: null, error: null, free_bytes: 120e6, low_space: true }));
  await open(page, "");
  await expect(page.locator(".notice", { hasText: "120 MB" })).toBeVisible();
});

test("no storage notice while everything is fine", async ({ page }) => {
  await open(page, "");
  await expect(page.locator(".page-head")).toBeVisible();
  await expect(page.locator(".notice", { hasText: "Messwerte" })).toHaveCount(0);
});
