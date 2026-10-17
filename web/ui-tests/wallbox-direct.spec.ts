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
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
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
    { service: "_modbus._tcp", name: "EVC-SIMULATED01", host: "192.168.178.61", port: 1502 },
    { service: "_modbus._tcp", name: "INVERTER", host: "192.168.178.62", port: 502, inverter: true }] });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await expect(page.locator('option[value="foxess_ac"]')).toHaveText("FoxESS AC EV Charger (auch von EKD verkauft)"); // #412
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await page.getByRole("button", { name: "Wallbox im Heimnetz suchen" }).click();
  await expect(page.locator(".found-list li").nth(1)).toContainText("dein Wechselrichter"); // not taken by mistake (#411)
  await expect(page.locator(".found-list li").first()).not.toContainText("Wechselrichter");
  await page.getByRole("button", { name: "Übernehmen" }).first().click();
  await expect(page.getByLabel("Adresse der Wallbox")).toHaveValue("192.168.178.61");
  await expect(page.getByLabel("Port")).toHaveValue("1502");
});

test("an unreachable wallbox names the ports that were tried", async ({ page, api }) => {
  api.override("/api/wallbox/check", { ...READABLE, port: null, outcome: "unreachable", tried_ports: [502, 1502], findings: {} });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await page.getByLabel("Adresse der Wallbox").fill("192.168.178.60");
  await page.getByRole("button", { name: "Wallbox prüfen" }).click();
  await expect(page.locator(".notice.error")).toContainText("502, 1502");
});

// #427: a refused connection points to another client, a name that cannot be resolved asks for the IP address
test("a refused connection and an unknown name get their own advice", async ({ page, api }) => {
  api.override("/api/wallbox/check", { ...READABLE, port: null, outcome: "connection_refused", tried_ports: [502, 1502],
    ports: { "502": "refused", "1502": "timeout" }, findings: {} });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await page.getByLabel("Adresse der Wallbox").fill("192.168.178.60");
  await page.getByRole("button", { name: "Wallbox prüfen" }).click();
  const refused = page.locator(".notice.warn", { hasText: "keine Modbus-Verbindung" });
  await expect(refused).toContainText("(Port 502)");
  await expect(refused).toContainText("bisherige Smartbox");
  await expect(refused).toContainText("vom Strom");

  // #431: an accepted connection without a proper answer, or one closed at once, names the smartbox as well
  for (const [outcome, text] of [["no_answer", "nicht richtig"], ["connections_taken", "schon ein anderes Gerät"]]) {
    api.override("/api/wallbox/check", { ...READABLE, outcome, findings: {} });
    await page.getByRole("button", { name: "Wallbox prüfen" }).click();
    const notice = page.locator(".notice", { hasText: text });
    await expect(notice).toContainText("bisherige Smartbox");
    await expect(notice).toContainText("vom Strom");
  }

  api.override("/api/wallbox/check", { ...READABLE, host: "EVC-example.local", port: null, outcome: "unresolved",
    tried_ports: [], ports: {}, local_name_in_app: true, findings: {} });
  await page.getByRole("button", { name: "Wallbox prüfen" }).click();
  await expect(page.locator(".notice.error")).toContainText("Namen, die auf .local enden");
});

test("another wallbox is pointed to the device reader", async ({ page }) => {
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await page.getByLabel("Welche Wallbox hast du?").selectOption("other");
  await page.getByRole("button", { name: "Gerät auslesen" }).click();
  await expect(page.locator(".page-head h1")).toHaveText("Gerät auslesen");
});

// #328: listening over OCPP, the wallbox connects to the direct address
test("the OCPP listener shows the address and what the wallbox reported", async ({ page, api }) => {
  const idle = { active: false, remaining_s: 0, connected: false, path: "/ocpp", charge_point: null, vendor: null, model: null,
    firmware: null, configuration_keys: 0, status: null, messages: 0 };
  api.override("/api/wallbox/ocpp", idle);
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await expect(page.locator(".notice", { hasText: "ohne Ladeprofil lädt sie ohne Begrenzung" })).toBeVisible();
  const connected = { ...idle, active: true, remaining_s: 840, connected: true, charge_point: "SN01", vendor: "FoxESS",
    model: "A022KP1", firmware: "1.16", configuration_keys: 42, status: "Available", messages: 7 };
  api.override("/api/wallbox/ocpp/start", connected);
  api.override("/api/wallbox/ocpp", connected);
  await page.getByRole("button", { name: "OCPP-Zuhörer starten (15 Minuten)" }).click();
  await expect(page.locator(".code-block")).toHaveText(/^ws:\/\/.+\/ocpp$/);
  await expect(page.locator(".notice", { hasText: "OpenAmpere hört zu" })).toBeVisible();
  await expect(page.locator(".facts")).toContainText("FoxESS A022KP1");
  await expect(page.getByText("Stell die Wallbox in der App des Herstellers wieder")).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});
