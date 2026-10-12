// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

// #324: reading any device in the home network, read-only
test("diagnostics leads to the device reader", async ({ page }) => {
  await open(page, "more/diagnostics");
  await page.getByRole("button", { name: /Gerät auslesen/ }).click();
  await expect(page.locator(".page-head h1")).toHaveText("Gerät auslesen");
});

test("found devices can be taken over and registers are shown with the device's answer", async ({ page, api }) => {
  api.override("/api/diagnostics/search", { available: true, devices: [
    { service: "_modbus._tcp", name: "EVC-SIMULATED01", host: "192.168.178.77", port: 1502 }] });
  api.override("/api/diagnostics/read", { read: { host: "192.168.178.77", port: 1502, unit: 1, function: 3,
    shared_connection: false, blocks: [
      { start: 4096, count: 4, ok: true, ms: 12, error: null, exception: null, words: [0x4130, 0x3232, 0x4b50, 0xffff],
        text: [{ address: 4096, text: "A022KP" }] },
      { start: 4100, count: 2, ok: false, ms: null, error: "no_answer", exception: 6, words: null }] } });
  await open(page, "more/device-reader");
  await page.getByRole("button", { name: "Im Heimnetz suchen" }).click();
  await page.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.getByLabel("Adresse des Geräts")).toHaveValue("192.168.178.77");
  await expect(page.getByLabel("Port")).toHaveValue("1502");
  await page.getByLabel("Startadresse").fill("0x1000");
  await page.getByRole("button", { name: "Lesen", exact: true }).click();
  await expect(page.locator(".register-table").first()).toContainText("0x1003");
  await expect(page.locator(".register-table").first()).toContainText("-1");  // 0xFFFF with sign
  await expect(page.locator(".reader-text")).toContainText("A022KP");
  await expect(page.getByText(/Gerät beschäftigt \(Ausnahme 6\)/)).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("numbers are checked before anything is sent", async ({ page }) => {
  await open(page, "more/device-reader");
  await page.getByLabel("Adresse des Geräts").fill("192.168.178.77");
  await page.getByLabel("Startadresse").fill("zwölf");
  await page.getByRole("button", { name: "Lesen", exact: true }).click();
  await expect(page.locator(".toast")).toContainText("als Zahlen angeben");
});
