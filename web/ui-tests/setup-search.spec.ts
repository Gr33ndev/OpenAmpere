// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, test } from "./fixtures";

// #311: a device whose only connection is taken (e.g. by the old IQ box) is "busy", not an unknown device. It can be
// the inverter or the wallbox, which the old box holds as well on EKD systems (#433)
test("the network search says when a device is busy and what to do", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, configured: false });
  api.override("/api/setup/scan", { devices: [
    { host: "192.168.178.20", port: 502, busy: true, model: null, manufacturer: null, driver: null, unit: null, serial: null },
    { host: "192.168.178.30", port: 502, busy: false, model: null, manufacturer: null, driver: null, unit: null, serial: null },
  ] });
  await page.goto("/#/"); // not configured: the setup wizard, without the menu
  await page.getByRole("button", { name: "Los geht's" }).click();
  await page.getByRole("button", { name: "Netzwerk durchsuchen" }).click();
  await expect(page.getByText("Modbus-Gerät, gerade belegt", { exact: true })).toBeVisible();
  await expect(page.getByText("Wechselrichter, gerade belegt")).toHaveCount(0);
  await expect(page.getByText("Unbekanntes Modbus-Gerät", { exact: true })).toBeVisible(); // the other one
  const hint = page.getByText("„Gerade belegt“", { exact: false });
  // the wallbox held by the IQ box, then other programs, then the IQ box, with the guide on a box that overwrites settings
  await expect(hint).toContainText(/Wechselrichter sein oder eine Wallbox.*IQ-Box.*Wallbox/);
  await expect(hint).toContainText(/zuerst, ob noch ein anderes Programm.*IQ-Box/);
  await expect(hint.getByRole("link", { name: "mehr dazu in der Anleitung" })).toHaveAttribute("href", /#die-bisherige-smartbox/);
  expect(await layoutProblems(page)).toEqual([]);
});

// #433: a FoxESS wallbox that announces itself is named as such and cannot be picked as the inverter
test("the network search names an announced wallbox and does not offer it", async ({ page, api }) => {
  const status = await demoRequest<Record<string, unknown>>("GET", "/api/status");
  api.override("/api/status", { ...status, configured: false });
  api.override("/api/setup/scan", { devices: [
    { host: "192.168.178.20", port: 502, busy: false, wallbox: false, model: "H3-10.0", manufacturer: "FoxESS", driver: "foxess", unit: 247, serial: "TEST0000001234" },
    { host: "192.168.178.40", port: 502, busy: false, wallbox: true, model: null, manufacturer: null, driver: null, unit: null, serial: null },
  ] });
  await page.goto("/#/");
  await page.getByRole("button", { name: "Los geht's" }).click();
  await page.getByRole("button", { name: "Netzwerk durchsuchen" }).click();
  const wallbox = page.getByRole("button", { name: /FoxESS-Wallbox/ });
  await expect(wallbox).toBeDisabled();
  await expect(wallbox).toContainText("192.168.178.40 · kein Wechselrichter");
  await expect(page.getByText("Dieses Gerät meldet sich im Netzwerk selbst als Wallbox", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: /FoxESS H3-10.0/ })).toBeEnabled();
  await expect(page.getByText("„Gerade belegt“", { exact: false })).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);
});
