// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { CarsView } from "../src/CarsPage";
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

async function view(): Promise<CarsView> {
  return demoRequest<CarsView>("GET", "/api/cars");
}

// #401: the car's values come from its manufacturer, late: always with their age
test("the devices tab shows the car with its state of charge, its age and the last charge", async ({ page }) => {
  await open(page, "devices");
  const card = page.locator(".card.car", { hasText: "CUPRA Born" });
  await expect(card.locator(".wallbox-soc")).toContainText("64");
  await expect(card).toContainText("vor 21 Minuten");
  await expect(card).toContainText("Beim Einstecken");
  await expect(card).toContainText("31 %");
  await expect(card).toContainText("26,4 kWh");
  await expect(card).toContainText("Gefahren seitdem");
  await expect(page.getByText("sind nicht live", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("a car whose login was refused points to its setup", async ({ page, api }) => {
  const base = await view();
  api.override("/api/cars", { ...base, cars: [{ ...base.cars[0], status: "auth",
    error: "Die Anmeldung wurde abgelehnt. Bitte E-Mail und Passwort prüfen." }] });
  await open(page, "devices");
  const card = page.locator(".card.car");
  await expect(card).toContainText("Anmeldung abgelehnt");
  await expect(card).toContainText("Bitte E-Mail und Passwort prüfen.");
  await card.getByRole("button", { name: "Auto einrichten" }).click();
  await expect(page).toHaveURL(/#\/more\/cars$/);
});

test("the setup lists the car without its password and explains the portal", async ({ page }) => {
  await open(page, "more/cars");
  await expect(page.getByText("CUPRA · …0001 · Mobile Wallbox")).toBeVisible();
  await page.getByText("Einmalig im Browser: das EU-Data-Act-Portal einrichten").click();
  await expect(page.getByRole("link", { name: "eu-data-act.drivesomethinggreater.com" })).toBeVisible();
  await page.getByRole("button", { name: "Ändern" }).click();
  await expect(page.getByLabel("Passwort")).toHaveValue("");
  await expect(page.getByText("Ausgewählt ist das Auto mit der Fahrgestellnummer …0001.", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

// #595: BMW and MINI need no password: a client ID from their portal and a login confirmed in the browser
test("a MINI is set up with a client ID and a login confirmed in the browser", async ({ page, api }) => {
  api.override("/api/cars/login", { login: "L1", url: "https://example.org/device?user_code=ABCD-EFGH",
    code: "ABCD-EFGH", expires_in: 300, interval: 1 });
  api.override("/api/cars/login/L1", { done: true });
  api.override("/api/cars/vehicles", { vehicles: [{ vin: "WMWTESTVIN0000001", name: "" }] });
  await open(page, "more/cars");
  await page.getByText("Einmalig im Browser: BMW CarData einrichten").click();
  await expect(page.getByRole("link", { name: "MINI" })).toHaveAttribute("href", /mini\.de/);
  await page.getByRole("button", { name: "Auto hinzufügen" }).click();
  const form = page.locator(".card.form");
  await form.locator("select").first().selectOption("bmw_cardata/mini");
  await expect(page.getByLabel("Passwort")).toHaveCount(0);
  const load = page.getByRole("button", { name: "Autos aus dem Konto laden" });
  await expect(load).toBeDisabled();
  await form.getByRole("textbox", { name: /^Client-ID/ }).fill("0a1b2c3d-4e5f-4a5b-8c7d-0123456789ab");
  await page.getByRole("button", { name: "Anmeldung starten" }).click();
  await expect(page.getByText("ABCD-EFGH")).toBeVisible();
  await expect(page.getByRole("link", { name: "diese Seite" })).toHaveAttribute("href", /user_code=ABCD-EFGH/);
  await expect(page.getByText("Anmeldung bestätigt.", { exact: false })).toBeVisible();
  await load.click();
  await expect(form.locator("select").nth(1)).toContainText("…0001");
  await expect(page.getByRole("button", { name: "Speichern" })).toBeEnabled();
  expect(await layoutProblems(page)).toEqual([]);
});

test("without cars the devices tab has no car section", async ({ page, api }) => {
  api.override("/api/cars", { ...(await view()), cars: [] });
  await open(page, "devices");
  await expect(page.locator(".card.car")).toHaveCount(0);
});
