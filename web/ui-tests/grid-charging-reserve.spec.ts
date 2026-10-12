// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #315: the inverter charges from the grid only up to the backup reserve, so OpenAmpere raises it while charging
test("while grid charging raised the reserve, both pages say so and that it is set back", async ({ page, api }) => {
  const charging = await demoRequest<Record<string, unknown>>("GET", "/api/charging");
  api.override("/api/charging", { ...charging, active: true, reserve_raised: { original: 20, raised: 61 } });
  await open(page, "devices/charging");
  await expect(page.getByText("vorübergehend von 20 % auf 61 % angehoben und stellt sie danach wieder zurück",
    { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  await open(page, "devices/battery");
  await expect(page.getByText("gerade für das Laden aus dem Netz vorübergehend auf 61 % angehoben", { exact: false }))
    .toBeVisible();
  await expect(page.getByText("Danach stellt OpenAmpere sie wieder auf 20 %", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("without a raised reserve there is no such note", async ({ page }) => {
  await open(page, "devices/battery");
  await expect(page.getByText("für das Laden aus dem Netz vorübergehend", { exact: false })).toHaveCount(0);
});

test("the charging page says when the inverter was found to stop at the reserve", async ({ page, api }) => {
  const charging = await demoRequest<Record<string, unknown>>("GET", "/api/charging");
  api.override("/api/charging", { ...charging, stops_at_reserve: true });
  await open(page, "devices/charging");
  await expect(page.getByText("Dein Wechselrichter lädt aus dem Netz nur bis zur Notstrom-Reserve", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});
