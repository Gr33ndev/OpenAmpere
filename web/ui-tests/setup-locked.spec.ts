// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, test } from "./fixtures";

// A damaged database whose password could not be taken over: no password form for anybody in the home network, but
// the command the owner runs on the server
test("after a damaged database without its password the app says to reset it on the server", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, database: { damaged: { ts: Date.now() / 1000 - 60,
    file: "/data/openampere.db.damaged-20260101-120000" }, restored: null, rollback: null } });
  api.override("/api/auth/status", { configured: false, authenticated: false, locked: true });
  await page.goto("/#/");
  await expect(page.getByText("dein bisheriges Passwort nicht übernehmen")).toBeVisible();
  await expect(page.locator("code", { hasText: "openampere reset-password" })).toBeVisible();
  await expect(page.locator("input[type=password]")).toHaveCount(0);
  await expect(page.getByText("Lege jetzt sofort ein neues Passwort fest")).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);

  api.override("/api/auth/status", { configured: false, authenticated: false }); // after reset-password
  await page.waitForTimeout(1600); // the app shares GET results for 1.5 s (getJson)
  await page.getByRole("button", { name: "Jetzt erneut versuchen" }).click();
  await expect(page.locator("input[type=password]")).toHaveCount(2);
});
