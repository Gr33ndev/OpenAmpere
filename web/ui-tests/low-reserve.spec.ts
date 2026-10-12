// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

const SETTINGS = { work_mode: "self_use", min_soc: 0, max_soc: 100, unreadable: [], external_change: null };

// #313: a backup reserve below 10 % only after a warning, every time it is set
test("a backup reserve below 10 % asks every time and is only sent after confirming", async ({ page, api }) => {
  api.override("/api/battery/settings", { ...SETTINGS, min_soc_on_grid: 20 });
  const sent: unknown[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().endsWith("/api/battery/settings")) sent.push(request.postDataJSON());
  });
  await open(page, "devices/battery");
  await page.getByRole("slider").first().fill("5");
  await expect(page.getByText("Die Notstrom-Reserve steht auf 5 %, also unter 10 %", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Übernehmen" }).click();
  const dialog = page.getByRole("dialog", { name: "Notstrom-Reserve unter 10 %?" });
  await expect(dialog).toContainText("Garantie");
  expect(await layoutProblems(page)).toEqual([]);
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  expect(sent).toEqual([]); // nothing sent without confirming

  await page.getByRole("button", { name: "Übernehmen" }).click();
  await page.getByRole("button", { name: "Trotzdem auf 5 % setzen" }).click();
  await expect.poll(() => sent).toEqual([{ min_soc_on_grid: 5, confirm_low_reserve: true }]);
});

test("a backup reserve below 10 % shows a lasting warning on the battery card and page", async ({ page, api }) => {
  api.override("/api/battery/settings", { ...SETTINGS, min_soc_on_grid: 5 });
  await open(page, "devices");
  await expect(page.getByText("Die Notstrom-Reserve steht auf 5 %, also unter 10 %. Das ist nicht gut für den Speicher",
    { exact: false })).toBeVisible();
  await open(page, "devices/battery");
  await expect(page.getByText("Er wird tiefer entladen", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});
