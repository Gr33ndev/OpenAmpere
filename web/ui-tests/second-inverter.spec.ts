// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Settings, Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #419: a second PV inverter that the inverter measures with a meter (SAJ Meter B)
async function withMeter(watts: number) {
  const status = await demoRequest<Status>("GET", "/api/status");
  return { ...status, external_pv_w: watts };
}

async function settings(values: Partial<Settings["values"]>) {
  const current = await demoRequest<Settings>("GET", "/api/settings");
  return { ...current, values: { ...current.values, ...values } };
}

test("a counted second inverter gets its own sun in the energy flow", async ({ page, api }) => {
  api.override("/api/settings", await settings({ "pv.external": true, "pv.external_name": "Garage" }));
  api.live({ pv_power: 4000, pv_external_power: 1500, pv_inputs: [{ power: 1500, voltage: 400, current: 3.8 },
    { power: 1000, voltage: 380, current: 2.6 }] });
  await open(page, "");
  const own = page.locator(".flow-node.sun");
  const second = page.locator(".flow-node.sun-external");
  await expect(own).toContainText("2,50 kW");
  await expect(own).toContainText("Wechselrichter");
  await expect(second).toContainText("1,50 kW");
  await expect(second).toContainText("Garage");
  await expect(page.locator(".flow")).toHaveAttribute("aria-label", /Garage 1,50 kW/);
  expect(await layoutProblems(page)).toEqual([]);
  await open(page, "more/installation"); // the PV card lists it next to the strings
  await expect(page.locator(".input-row", { hasText: "Garage" })).toContainText("1,50 kW");
});

test("without a second inverter the energy flow has one sun", async ({ page }) => {
  await open(page, "");
  await expect(page.locator(".flow-node.sun")).toBeVisible();
  await expect(page.locator(".flow-node.sun-external")).toHaveCount(0);
});

test("the overview points to the switch while the meter reads power", async ({ page, api }) => {
  api.override("/api/status", await withMeter(800));
  await open(page, "");
  const hint = page.locator(".notice", { hasText: "Zweiter Wechselrichter?" });
  await expect(hint).toContainText("0,80 kW");
  await hint.getByRole("button", { name: "Ausblenden" }).click();
  await expect(hint).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".flow")).toBeVisible();
  await expect(page.locator(".notice", { hasText: "Zweiter Wechselrichter?" })).toHaveCount(0);
});

test("the switch is offered on the PV page when the inverter has the meter", async ({ page, api }) => {
  await open(page, "more/pv");
  await expect(page.getByText("Zweiten Wechselrichter mitzählen")).toHaveCount(0); // a FoxESS: no such meter
  api.override("/api/status", await withMeter(300));
  let sent: unknown = null;
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "PUT") sent = route.request().postDataJSON();
    await route.fallback();
  });
  await page.reload();
  await expect(page.getByText("Der Zähler misst gerade 0,30 kW.")).toBeVisible();
  await page.locator(".switch-row", { hasText: "Zweiten Wechselrichter mitzählen" }).getByRole("switch").click();
  await page.getByLabel("Name").fill("Garage");
  await page.locator(".card.form", { hasText: "Zweiten Wechselrichter mitzählen" }).getByRole("button", { name: "Speichern" }).click();
  await expect.poll(() => sent).toMatchObject({ "pv.external": true, "pv.external_name": "Garage" });
  expect(await layoutProblems(page)).toEqual([]);
});

test("the connection test mentions the meter for a second inverter", async ({ page }) => {
  await page.route("**/api/setup/test", (route) => route.fulfill({ json: { ok: true, device: { manufacturer: "SAJ",
    model: "H2", firmware: "1.0", rated_power_w: 10000, supports_control: true }, label: null,
    sample: { pv_power: 2000, battery_soc: 50, pv_external_power: 0 } } }));
  await open(page, "more/connection");
  await page.getByPlaceholder(/192\.168\.178\.50/).fill("192.168.178.50");
  await page.getByRole("button", { name: "Verbindung testen" }).click();
  await expect(page.locator(".notice", { hasText: "Meter B" })).toContainText("Modulfelder benennen");
});
