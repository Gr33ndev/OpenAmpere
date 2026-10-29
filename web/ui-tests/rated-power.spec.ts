// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { ExportLimit, Settings } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #492: an SAJ whose reported rated power does not fit its type: the owner confirms it before changing the limit
test("the rated power is confirmed before the export limit, and the feed-in rule can be changed after it", async ({ page, api }) => {
  const limit = await demoRequest<ExportLimit>("GET", "/api/grid/export-limit");
  api.override("/api/grid/export-limit", { ...limit, rated_power_w: 800, rated_power_reported_w: 800, rated_power_confirmed: false });
  // the server refuses a save with an old revision, as the real one does
  const settings = await demoRequest<Settings>("GET", "/api/settings");
  let revision = 1;
  const refused: string[] = [];
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { ...settings, revision } });
    const changes = route.request().postDataJSON() as Record<string, unknown>;
    if (changes._revision !== revision) {
      refused.push(Object.keys(changes).join(","));
      return route.fulfill({ status: 409, json: { detail: "Die Einstellungen wurden inzwischen auf einem anderen Gerät geändert." } });
    }
    revision += 1;
    return route.fulfill({ json: { ...settings, revision } });
  });

  await open(page, "more/export-limit");
  await expect(page.getByText("Dein Wechselrichter meldet eine Nennleistung von 800 W.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Einspeisebegrenzung ändern" })).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);

  await page.getByPlaceholder("z. B. 8000").fill("8000");
  await page.getByRole("button", { name: "Nennleistung bestätigen" }).click();
  await expect.poll(() => revision).toBe(2);
  await page.getByRole("button", { name: /Fester Wert vom Netzbetreiber/ }).click();
  await expect.poll(() => revision).toBe(3);
  expect(refused).toEqual([]);
});
