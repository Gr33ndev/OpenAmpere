// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

const READABLE = { model: "foxess_ac", label: "FoxESS AC EV Charger", host: "192.168.178.60", port: 502, unit: 1,
  outcome: "readable", tried_ports: [502], findings: { model: "A022KP1", firmware: "1.16", status: "idle",
    max_power_kw: 22, min_current_a: 6, max_current_a: 32, work_mode: "plug_and_charge", fallback_current_a: 6,
    validity_s: 60, phase_switching: true } };

// #325: the wallbox setup offers evcc or, as a read-only preview, a direct check
test("the direct check shows what the wallbox answers and leads to the report", async ({ page, api }) => {
  api.override("/api/wallbox/check", READABLE);
  await open(page, "more/wallbox");
  await expect(page.getByText("Messwerte von OpenAmpere in evcc übernehmen")).toBeVisible();  // evcc stays the default
  await page.getByRole("button", { name: "direkt (Vorschau)" }).click();
  await expect(page.locator(".notice", { hasText: "Vorschau" })).toBeVisible();
  await page.getByLabel("Adresse der Wallbox").fill("192.168.178.60");
  await page.getByLabel("Ist die Wallbox mit dem Wechselrichter verkabelt").selectOption("yes");
  await expect(page.locator(".notice", { hasText: "Eigenverbrauchs-Modus" })).toBeVisible();
  await page.getByRole("button", { name: "Wallbox prüfen" }).click();
  await expect(page.locator(".notice", { hasText: "Gefunden" })).toBeVisible();
  const facts = page.locator(".facts");
  await expect(facts).toContainText("A022KP1");
  await expect(facts).toContainText("beim Einstecken");
  await expect(facts).toContainText("6,0–32,0 A");
  expect(await layoutProblems(page)).toEqual([]);
  await page.getByRole("button", { name: "Bericht erstellen" }).click();
  await expect(page.locator(".page-head h1")).toHaveText("Diagnose");
});

test("a wallbox found in the home network is taken over like in the device reader", async ({ page, api }) => {
  api.override("/api/diagnostics/search", { available: true, devices: [
    { service: "_modbus._tcp", name: "EVC-SIMULATED01", host: "192.168.178.61", port: 1502 }] });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt (Vorschau)" }).click();
  await page.getByRole("button", { name: "Wallbox im Heimnetz suchen" }).click();
  await page.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.getByLabel("Adresse der Wallbox")).toHaveValue("192.168.178.61");
  await expect(page.getByLabel("Port")).toHaveValue("1502");
});

test("an unreachable wallbox names the ports that were tried", async ({ page, api }) => {
  api.override("/api/wallbox/check", { ...READABLE, port: null, outcome: "unreachable", tried_ports: [502, 1502], findings: {} });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt (Vorschau)" }).click();
  await page.getByLabel("Adresse der Wallbox").fill("192.168.178.60");
  await page.getByRole("button", { name: "Wallbox prüfen" }).click();
  await expect(page.locator(".notice.error")).toContainText("502, 1502");
});

test("another wallbox is pointed to the device reader", async ({ page }) => {
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt (Vorschau)" }).click();
  await page.getByRole("combobox").first().selectOption("other");  // the choice of the wallbox model
  await page.getByRole("button", { name: "Gerät auslesen" }).click();
  await expect(page.locator(".page-head h1")).toHaveText("Gerät auslesen");
});
