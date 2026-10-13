// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #353: without login the status only says that something happened to the database, not when or which file
test("database notices work without the details only shown after login", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, database: { damaged: true, restored: true, rollback: true } });
  await open(page, "");
  const notice = page.locator(".top-notice");
  await expect(notice).toContainText("Die Datenbank war beschädigt");
  await expect(notice).toContainText("beim Start nicht mehr lesen"); // no time
  await expect(notice).toContainText("von vor dem Update");
  await expect(notice).toContainText("Beim Start war die Datenbank neuer");
  await expect(notice).not.toContainText("Datenordner");
  await expect(notice).not.toContainText("undefined");
  expect(await layoutProblems(page)).toEqual([]);

  await open(page, "more/data");
  await expect(page.getByText("Zuletzt wiederhergestellt")).toHaveCount(0);
  await expect(page.getByText("undefined")).toHaveCount(0);
});

test("the last restore names the kept file in the data folder", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, database: { damaged: null, rollback: null,
    restored: { ts: Date.now() / 1000 - 60, kept: "openampere.db.before-restore-20260101-120000" } } });
  await open(page, "more/data");
  const hint = page.getByText("Zuletzt wiederhergestellt");
  await expect(hint).toContainText("im Datenordner von OpenAmpere");
  await expect(hint).toContainText("openampere.db.before-restore-20260101-120000");
});
