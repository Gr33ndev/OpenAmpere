// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, test } from "./fixtures";

// #311: an inverter whose only connection is taken (e.g. by the old IQ box) is "busy", not an unknown device
test("the network search says when an inverter is busy and what to do", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, configured: false });
  api.override("/api/setup/scan", { devices: [
    { host: "192.168.178.20", port: 502, busy: true, model: null, manufacturer: null, driver: null, unit: null, serial: null },
    { host: "192.168.178.30", port: 502, busy: false, model: null, manufacturer: null, driver: null, unit: null, serial: null },
  ] });
  await page.goto("/#/"); // not configured: the setup wizard, without the menu
  await page.getByRole("button", { name: "Los geht's" }).click();
  await page.getByRole("button", { name: "Netzwerk durchsuchen" }).click();
  await expect(page.getByText("Wechselrichter, gerade belegt", { exact: true })).toBeVisible();
  await expect(page.getByText("Unbekanntes Modbus-Gerät", { exact: true })).toBeVisible(); // the other one
  const hint = page.getByText("„Gerade belegt“", { exact: false });
  await expect(hint).toContainText("IQ-Box"); // the old box or another program can take the connections
  await expect(hint).toContainText("ein anderes Programm, das den Wechselrichter abfragt");
  expect(await layoutProblems(page)).toEqual([]);
});
