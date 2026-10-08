import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

const FILE = "/data/openampere.db.damaged-20260101-120000";

// #165: after a damaged database the owner sees what happened, where the old file is and can restore a backup
test("damaged database notice explains what happened and offers a restore", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, database: { damaged: { ts: Date.now() / 1000 - 60, file: FILE }, restored: null, rollback: null } });
  await open(page, "");
  const notice = page.locator(".top-notice");
  await expect(notice).toContainText("Die Datenbank war beschädigt");
  await expect(notice).toContainText(FILE);
  await expect(notice.getByRole("button", { name: "Sicherungsdatei auswählen" })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

// #166: an older version after a rollback uses the copy from before the update and says so
test("rollback notice explains which database is used", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, database: { damaged: null, restored: null,
    rollback: { ts: Date.now() / 1000 - 60, version: 2, kept: "/data/openampere.db.newer-2-20260101-120000" } } });
  await open(page, "");
  const notice = page.locator(".top-notice");
  await expect(notice).toContainText("von vor dem Update");
  await expect(notice).toContainText("openampere.db.newer-2-20260101-120000");
  expect(await layoutProblems(page)).toEqual([]);
});

test("restoring a backup asks before replacing the data", async ({ page }) => {
  await open(page, "more/data");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Sicherungsdatei auswählen" }).click();
  await (await chooser).setFiles({ name: "openampere-backup.db", mimeType: "application/octet-stream", buffer: Buffer.from("x") });
  const dialog = page.getByRole("dialog", { name: "Sicherung wiederherstellen?" });
  await expect(dialog).toContainText("openampere-backup.db");
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  await expect(dialog).toBeHidden();
});
