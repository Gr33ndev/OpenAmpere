// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, open, test } from "./fixtures";

// #221: forms keep what the owner typed

test("the grid charging form keeps unsaved changes when the page refreshes its data", async ({ page }) => {
  await page.clock.install();
  await open(page, "devices/charging");
  const slider = page.locator(".slider input[type=range]").first();
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  const edited = await slider.inputValue();
  await expect(page.locator(".unsaved")).toBeVisible();
  await page.clock.fastForward(31_000); // the page polls every 30 s
  await page.waitForTimeout(500);
  expect(await slider.inputValue()).toBe(edited);
  await expect(page.locator(".unsaved")).toBeVisible();
});

test("a decimal comma can be typed for the usable capacity", async ({ page }) => {
  await open(page, "devices/charging");
  const field = page.locator("label.field", { hasText: "Nutzbare Speichergröße" }).locator("input");
  await field.fill("");
  await field.pressSequentially("9,5");
  await expect(field).toHaveValue("9,5");
  await field.blur();
  await expect(field).toHaveValue("9,5");
});

test("a decimal comma can be typed for an advance payment", async ({ page }) => {
  await open(page, "more/billing");
  const field = page.locator(".input-unit", { hasText: "€" }).locator("input").first();
  await field.fill("");
  await field.pressSequentially("85,50");
  await expect(field).toHaveValue("85,50");
  await field.blur();
  await expect(field).toHaveValue("85,5");
});

test("saving the EEG card keeps unsaved changes of a tariff", async ({ page, api }) => {
  api.override("/api/settings", await demoRequest("GET", "/api/settings"));
  await open(page, "more/tariff");
  const price = page.locator(".card.form", { has: page.locator("select") }).first().locator("input[inputmode=decimal]").first();
  await price.fill("40,00");
  const eeg = page.locator(".card.form", { has: page.locator("h2") }).first();
  await eeg.locator(".segmented button", { hasText: "Volleinspeisung" }).click();
  await eeg.locator("button.btn", { hasText: "Speichern" }).click();
  await page.waitForTimeout(1500);
  await expect(price).toHaveValue("40,00");
  await expect(page.locator(".unsaved")).toBeVisible();
});

test("a connection test for an address that was changed meanwhile does not count", async ({ page }) => {
  await page.route("**/api/setup/test", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500)); // detecting the device takes a while
    await route.fulfill({ json: { ok: true, device: { manufacturer: "Prüfgerät", model: "X1", firmware: "1.0",
      rated_power_w: 10000, supports_control: true }, label: null, sample: null } });
  });
  await open(page, "more/connection");
  const address = page.getByPlaceholder(/192\.168\.178\.50/);
  await address.fill("192.168.178.50");
  await page.getByRole("button", { name: "Verbindung testen" }).click();
  await address.fill("192.168.178.51"); // corrected while the test of .50 is still running
  await page.waitForTimeout(2500);
  await expect(page.getByText("Prüfgerät X1")).toHaveCount(0); // the device found at .50, not .51
});

// #522: an Enphase gateway needs an access token: the form asks for it only then, and the test sends it
test("the connection form asks for the Enphase token and sends it with the test", async ({ page }) => {
  await page.route("**/api/setup/drivers", (route) => route.fulfill({ json: { drivers: [
    { key: "auto", label: "Automatisch erkennen" }, { key: "enphase", label: "Enphase IQ Gateway mit IQ Battery (nur Anzeige)" }] } }));
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/setup/test", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({ json: { ok: false, error: "Das Enphase-Gateway lehnt das Zugangs-Token ab." } });
  });
  await open(page, "more/connection");
  await page.getByPlaceholder(/192\.168\.178\.50/).fill("192.168.178.70");
  await page.getByRole("button", { name: /Erweitert/ }).click();
  await expect(page.getByLabel("Zugangs-Token von Enphase")).toHaveCount(0);  // only for Enphase
  await page.getByLabel("Gerätetyp").selectOption("enphase");
  await page.getByLabel("Zugangs-Token von Enphase").fill("eyJ.test.token");
  await expect(page.getByText(/entrez\.enphaseenergy\.com/)).toBeVisible();
  await page.getByRole("button", { name: "Verbindung testen" }).click();
  await expect.poll(() => sent).toMatchObject({ driver: "enphase", token: "eyJ.test.token" });
});

// #539: the certificate the new token went to in the test is saved with the token
test("saving an Enphase connection keeps the certificate of the test with the token", async ({ page }) => {
  await page.route("**/api/setup/drivers", (route) => route.fulfill({ json: { drivers: [
    { key: "auto", label: "Automatisch erkennen" }, { key: "enphase", label: "Enphase IQ Gateway mit IQ Battery (nur Anzeige)" }] } }));
  await page.route("**/api/setup/test", (route) => route.fulfill({ json: { ok: true, label: null, sample: null,
    cert_sha256: "ab".repeat(32), device: { manufacturer: "Enphase", model: "IQ Gateway", firmware: "8.2", driver: "enphase",
      unit: 0, supports_control: false } } }));
  let saved: Record<string, unknown> | null = null;
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    saved = route.request().postDataJSON();
    await route.fulfill({ json: { ok: true } });
  });
  await open(page, "more/connection");
  await page.getByPlaceholder(/192\.168\.178\.50/).fill("192.168.178.70");
  await page.getByRole("button", { name: /Erweitert/ }).click();
  await page.getByLabel("Gerätetyp").selectOption("enphase");
  await page.getByLabel("Zugangs-Token von Enphase").fill("eyJ.test.token");
  await page.getByRole("button", { name: "Verbindung testen" }).click();
  await page.locator("button.btn.primary", { hasText: "Speichern" }).click();  // the form's, once the test passed
  await expect.poll(() => saved).toMatchObject({ "inverter.token": "eyJ.test.token", "inverter.cert_sha256": "ab".repeat(32) });
});
