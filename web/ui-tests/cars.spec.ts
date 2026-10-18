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
  await expect(page.getByText("mindestens 15 Minuten alt")).toBeVisible();
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
  await page.getByText("Einmalig im Browser: das Portal einrichten").click();
  await expect(page.getByRole("link", { name: "eu-data-act.drivesomethinggreater.com" })).toBeVisible();
  await page.getByRole("button", { name: "Ändern" }).click();
  await expect(page.getByLabel("Passwort")).toHaveValue("");
  await expect(page.getByText("Ausgewählt ist das Auto mit der Fahrgestellnummer …0001.", { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("without cars the devices tab has no car section", async ({ page, api }) => {
  api.override("/api/cars", { ...(await view()), cars: [] });
  await open(page, "devices");
  await expect(page.locator(".card.car")).toHaveCount(0);
});
