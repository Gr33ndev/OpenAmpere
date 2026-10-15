// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { NativeWallbox, WallboxesView } from "../src/NativeWallbox";
import { expect, layoutProblems, open, test } from "./fixtures";

const READING = { state: "charging", connected: true, charging: true, power_w: 5520, voltage_v: [230, 230, 230],
  current_a: [8, 8, 8], installed_phases: 3, active_phases: 3, session_kwh: 2.4, session_s: 1800, temperature_c: 22,
  set_current_a: 8, max_current_a: 16, steps_a: [6, 8, 10, 13, 16], firmware: "1.0.0", cp_v: 6.6 };

async function view(): Promise<WallboxesView> {
  return demoRequest<WallboxesView>("GET", "/api/wallboxes");
}

// a portable wallbox is unplugged most of the time: shown as away, without old values
test("an unplugged portable wallbox is shown as away on the devices tab", async ({ page }) => {
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Mobile Wallbox" });
  await expect(card).toContainText("nicht erreichbar seit");
  await expect(card).toContainText("verbindet sich von selbst wieder");
  await expect(card.locator(".facts")).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);
});

test("a charging wallbox shows its current and the mode can be changed", async ({ page, api }) => {
  const base = await view();
  const charging: NativeWallbox = { ...base.wallboxes[0], online: true, offline_since: null, error: null,
    reading: READING as NativeWallbox["reading"] };
  api.override("/api/wallboxes", { ...base, wallboxes: [charging] });
  api.override("/api/wallboxes/demo0001/mode", { ...base, wallboxes: [{ ...charging, mode: "now" }] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Mobile Wallbox" });
  await expect(card).toContainText("lädt mit 5,52 kW");
  await expect(card.locator(".facts")).toContainText("8 A (höchstens 16 A)");
  await card.getByRole("button", { name: "Sofort" }).click();
  await expect(card).toContainText("Lädt sofort mit voller Leistung");
});

test("a portable wallbox is added: search, check the connection, save", async ({ page, api }) => {
  const base = await view();
  api.override("/api/wallboxes/search", { available: true, devices: [
    { device_id: "simulated0000000001", host: "192.168.178.61", product_key: null }] });
  api.override("/api/wallboxes/test", { reading: { ...READING, charging: false, power_w: 0 } });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await expect(page.getByText("Mobile Wallbox").first()).toBeVisible();  // the one already set up
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  // the steps to the local key are folded in and open with a tap, with a link to the detailed guide
  const howTo = page.locator("details", { hasText: "So bekommst du den Local Key" });
  await expect(howTo).not.toHaveAttribute("open", "");
  await expect(howTo.getByText("API Explorer")).toBeHidden();
  await howTo.getByText("So bekommst du den Local Key").click();
  await expect(howTo.getByText("API Explorer")).toBeVisible();
  await expect(howTo.getByRole("link", { name: "Ausführliche Anleitung öffnen" }))
    .toHaveAttribute("href", /docs\/wallbox-direct\.de\.md$/);
  await page.getByRole("button", { name: "Tuya-Wallbox im Heimnetz suchen" }).click();
  await page.getByRole("button", { name: "Übernehmen" }).click();
  const address = page.getByLabel("IP-Adresse der Wallbox");
  await expect(address).toHaveValue("192.168.178.61");
  // a keyboard with a dot (the decimal one shows a comma in German); a typed comma becomes a dot
  await expect(address).toHaveAttribute("inputmode", "url");
  await address.fill("192,168,178,61");
  await expect(address).toHaveValue("192.168.178.61");
  const save = page.getByRole("button", { name: "Speichern" });
  await expect(save).toBeDisabled();  // the local key is still missing
  await page.getByLabel("Local Key").fill("0123456789abcdef");
  await page.getByRole("button", { name: "Verbindung prüfen" }).click();
  await expect(page.locator(".notice.ok")).toContainText("3 Phasen");
  expect(await layoutProblems(page)).toEqual([]);
  api.override("/api/wallboxes", { ...base, wallboxes: [...base.wallboxes, { ...base.wallboxes[0], id: "demo0002",
    name: "Mobile Wallbox 2", host: "192.168.178.61" }] });
  await save.click();
  await expect(page.getByText("Mobile Wallbox 2")).toBeVisible();
});

test("a search that is not possible says so instead of finding nothing", async ({ page, api }) => {
  api.override("/api/wallboxes/search", { available: false, devices: [] });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByRole("button", { name: "Tuya-Wallbox im Heimnetz suchen" }).click();
  await expect(page.getByText("Die Suche ist auf diesem Rechner gerade nicht möglich")).toBeVisible();
  await expect(page.getByText("Keine Tuya-Wallbox gefunden")).toHaveCount(0);
});

// one form for every wallbox: what is asked depends on the wallbox chosen
test("the local key is asked only for a Tuya wallbox, the FoxESS check stays in the same form", async ({ page }) => {
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  const which = page.getByLabel("Welche Wallbox hast du?");
  await expect(which).toHaveValue("de_tuya");  // the one OpenAmpere controls comes first
  await expect(page.getByLabel("Local Key")).toBeVisible();
  const element = await which.elementHandle();
  await which.selectOption("foxess_ac");
  // the choice stays the same element while the fields below change (focus and the open list are kept)
  expect(await element?.evaluate((e) => e.isConnected)).toBe(true);
  await expect(page.getByLabel("Local Key")).toHaveCount(0);
  await expect(page.getByText("So bekommst du den Local Key")).toHaveCount(0);
  await expect(page.locator(".notice", { hasText: "Vorschau" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Wallbox prüfen" })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

// a backup restored on another server: the stored local key cannot be read, the wallbox waits for a new one (#391)
test("a wallbox whose local key cannot be read asks for it again and is saved with a new one", async ({ page, api }) => {
  const base = await view();
  const lost: NativeWallbox = { ...base.wallboxes[0], local_key_set: false, online: null, offline_since: null,
    error: null, reading: null };
  api.override("/api/wallboxes", { ...base, wallboxes: [lost] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Mobile Wallbox" });
  await expect(card.locator(".hint", { hasText: "Local Key neu eintragen" })).toBeVisible();
  await expect(card.getByText("liest und steuert OpenAmpere die Wallbox nicht")).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  await card.getByRole("button", { name: "Local Key neu eintragen" }).click();

  await expect(page).toHaveURL(/#\/more\/wallbox$/);
  await page.getByRole("button", { name: "direkt", exact: true }).click();  // the demo has evcc, too
  await expect(page.getByText("nach dem Wiederherstellen einer Sicherung")).toBeVisible();
  await page.getByRole("button", { name: "Local Key neu eintragen" }).click();
  await expect(page.getByLabel("Name")).toHaveValue("Mobile Wallbox");  // the other settings are kept
  await expect(page.getByText("So bekommst du den Local Key")).toBeVisible();  // the steps to the key are offered
  const save = page.getByRole("button", { name: "Speichern", exact: true });
  await expect(save).toBeDisabled();
  await page.getByLabel("Local Key").fill("0123456789abcdef");
  api.override("/api/wallboxes", { ...base, wallboxes: [{ ...lost, local_key_set: true }] });
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes") && r.method() === "PUT");
  await save.click();
  expect((await sent).postDataJSON().wallboxes[0]).toMatchObject({ id: "demo0001", local_key: "0123456789abcdef" });
  await expect(page.getByText("Mobile Wallbox").first()).toBeVisible();
  await expect(page.getByText("liest und steuert OpenAmpere die Wallbox nicht")).toHaveCount(0);
});

test("without a car the card says that the mode applies once one is plugged in", async ({ page, api }) => {
  const base = await view();
  const waiting: NativeWallbox = { ...base.wallboxes[0], online: true, offline_since: null, error: null,
    reading: { ...READING, state: "idle", connected: false, charging: false, power_w: 0 } as NativeWallbox["reading"] };
  api.override("/api/wallboxes", { ...base, wallboxes: [waiting] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Mobile Wallbox" });
  await expect(card).toContainText("kein Fahrzeug angeschlossen");
  await expect(card).toContainText("gilt, sobald du ein Auto einsteckst");
});
