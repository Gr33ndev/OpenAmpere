// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #377: an SAJ inverter offers only two work modes and charges from the grid through a time slot
async function sajStatus() {
  const status = await demoRequest<Status>("GET", "/api/status");
  return { ...status, device: { ...status.device!, manufacturer: "SAJ", model: "H2 5-10K S3", driver: "saj",
    work_modes: ["self_use", "backup"], charging_continues_min: 10, backup_reserve: true } };
}

test("only the work modes of the device are offered", async ({ page, api }) => {
  api.override("/api/status", await sajStatus());
  await open(page, "devices/battery");
  await expect(page.getByRole("button", { name: /Eigenverbrauch/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Notstrom/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Einspeisung/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Spitzen/ })).toHaveCount(0);
});

test("grid charging on an SAJ says how long it keeps charging without OpenAmpere", async ({ page, api }) => {
  api.override("/api/status", await sajStatus());
  await open(page, "devices/charging");
  const notice = page.locator(".notice", { hasText: "SAJ-Wechselrichter haben keine Fernsteuerung" });
  await expect(notice).toContainText("bis zu 10 Minuten");
  await expect(notice).toContainText("jede Woche");
  expect(await layoutProblems(page)).toEqual([]);
});

test("a FoxESS keeps the remote control hint and every work mode", async ({ page }) => {
  await open(page, "devices/charging");
  await expect(page.locator(".notice", { hasText: "keine Fernsteuerung" })).toHaveCount(0);
  await open(page, "devices/battery");
  await expect(page.getByRole("button", { name: /Einspeisung/ })).toBeVisible();
});

// #380: in backup mode an SAJ keeps its own reserve, shown and set while that mode is chosen
test("the reserve of the backup mode is shown with that mode", async ({ page, api }) => {
  api.override("/api/status", await sajStatus());
  api.override("/api/battery/settings", { work_mode: "backup", min_soc: 5, max_soc: 100, min_soc_on_grid: 20,
    backup_soc: 80, unreadable: [], external_change: null });
  await open(page, "devices/battery");
  await expect(page.getByText("Reserve im Modus Notstromreserve")).toBeVisible();
  await expect(page.getByText("Im Modus „Notstromreserve“ gilt bei deinem Wechselrichter die eigene Reserve")).toBeVisible();
  await expect(page.getByText("Hält eine eigene Reserve für Stromausfälle zurück")).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  await page.getByRole("button", { name: /Eigenverbrauch/ }).click();
  await expect(page.getByText("Reserve im Modus Notstromreserve")).toHaveCount(0);
});

test("a FoxESS has no reserve of its own for the backup mode", async ({ page, api }) => {
  api.override("/api/battery/settings", { work_mode: "backup", min_soc: 10, max_soc: 100, min_soc_on_grid: 20,
    unreadable: [], external_change: null });
  await open(page, "devices/battery");
  await expect(page.getByText("Speicher wird nur geladen und für Stromausfälle voll gehalten.")).toBeVisible();
  await expect(page.getByText("Reserve im Modus Notstromreserve")).toHaveCount(0);
});
