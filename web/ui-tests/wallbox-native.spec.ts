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
  await expect(card).toContainText("Lädt sofort mit dem Ladestrom unten");
});

// the owner chooses the highest current per mode, e.g. 8 A for "fast" (#402)
test("the highest current is chosen per mode, with the steps of the wallbox", async ({ page, api }) => {
  const base = await view();
  const charging: NativeWallbox = { ...base.wallboxes[0], online: true, offline_since: null, error: null, mode: "now",
    mode_current_a: { now: 8 }, reading: READING as NativeWallbox["reading"] };
  api.override("/api/wallboxes", { ...base, wallboxes: [charging] });
  api.override("/api/wallboxes/demo0001/mode", { ...base, wallboxes: [{ ...charging, mode_current_a: { now: 13 } }] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Mobile Wallbox" });
  const current = card.getByRole("group", { name: "Höchster Ladestrom in diesem Modus" });
  await expect(current.getByRole("button")).toHaveText(["6 A", "8 A", "10 A", "13 A", "16 A"]);
  await expect(current.getByRole("button", { name: "8 A" })).toHaveAttribute("aria-pressed", "true");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/demo0001/mode"));
  await current.getByRole("button", { name: "13 A" }).click();
  expect((await sent).postDataJSON()).toEqual({ mode: "now", current_a: 13 });
  await expect(current.getByRole("button", { name: "13 A" })).toHaveAttribute("aria-pressed", "true");

  // "off" and "manual" have no current of their own
  api.override("/api/wallboxes", { ...base, wallboxes: [{ ...charging, mode: "off" }] });
  await page.reload();
  await expect(card.getByRole("group", { name: "Höchster Ladestrom in diesem Modus" })).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);
});

test("the waiting time before solar charging pauses is set in the wallbox settings", async ({ page, api }) => {
  api.override("/api/wallboxes", await view()); // also the answer to the save
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Ändern" }).first().click();
  const field = page.getByLabel("Solar-Laden pausieren nach (Minuten)");
  await expect(field).toHaveValue("10");
  await field.fill("20");
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes") && r.method() === "PUT");
  await page.getByRole("button", { name: "Speichern" }).click();
  const item = (await sent).postDataJSON().wallboxes.find((w: { id: string }) => w.id === "demo0001");
  expect(item).toMatchObject({ stop_after_min: 20, mode_current_a: { now: 8 } });
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
  // the FoxESS charger is shown and can be controlled as a test once added (#465, #503), the check is the other way
  await expect(which.locator("optgroup", { has: page.locator('option[value="foxess_ac"]') }))
    .toHaveAttribute("label", "Anzeigen, Steuern als Test");
  await expect(page.getByText("Nach dem Hinzufügen schaltest du bei der Wallbox die echte Steuerung ein.")).toBeVisible();
  await expect(page.getByText("Steuern kann OpenAmpere sie noch nicht")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Einrichten" })).toBeVisible();
  await expect(page.getByLabel("Port")).toHaveValue("502");
  await page.getByRole("button", { name: "Nur prüfen (Bericht)" }).click();
  expect(await element?.evaluate((e) => e.isConnected)).toBe(true);
  await expect(page.locator(".notice", { hasText: "Vorschau" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Wallbox prüfen" })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

// #439: the FoxESS AC EV Charger is only read; #440: its charging modes are a simulation, shown with what OpenAmpere
// would do next to what the charger really does
test("a FoxESS charger offers its modes as a simulation and shows the plan next to its real values", async ({ page, api }) => {
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Garage" });
  await expect(card.locator(".notice")).toContainText("Nur eine Simulation: OpenAmpere schreibt nichts an diese Wallbox.");
  await expect(card.locator(".notice")).toContainText("zum Beispiel sobald du ein Auto einsteckst");
  await expect(card.getByRole("button", { name: "Von Hand" })).toHaveAttribute("aria-pressed", "true");
  await expect(card).toContainText("Wähle einen anderen Lademodus, um auszuprobieren, wie OpenAmpere sie steuern würde.");
  await expect(card.locator(".facts")).toContainText("über App oder Karte");
  await expect(card.locator(".facts")).toContainText("32 A");
  await expect(card.locator(".facts")).not.toContainText("Simulation");  // "manual": nothing simulated
  expect(await layoutProblems(page)).toEqual([]);

  const base = await view();
  const fox = base.wallboxes.find((w) => w.model === "foxess_ac") as NativeWallbox;
  const planned: NativeWallbox = { ...fox, mode: "pv",
    simulation: { state: "charging", current_a: 10, phases: 1, power_w: 2300 } };
  api.override("/api/wallboxes/demo0003/mode", { ...base, wallboxes: [base.wallboxes[0], planned] });
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/demo0003/mode"));
  await card.getByRole("button", { name: "Solar", exact: true }).click();
  expect((await sent).postDataJSON()).toEqual({ mode: "pv" });
  await expect(card).toContainText("Simulation: OpenAmpere würde nur mit Solarüberschuss laden.");
  await expect(card.locator(".facts")).toContainText("OpenAmpere würde jetzt mit 10 A auf 1 Phase laden, etwa 2,30 kW.");
  // the highest current of the mode: the usual steps up to the owner's limit, not every whole ampere of the charger
  const current = card.getByRole("group", { name: "Höchster Ladestrom in diesem Modus" });
  await expect(current.getByRole("button")).toHaveText(["6 A", "8 A", "10 A", "13 A", "16 A"]);
  expect(await layoutProblems(page)).toEqual([]);

  // the charger charges by itself as it is set; the simulation would have stopped it
  const charging: NativeWallbox = { ...planned, reading: { ...fox.reading, state: "charging", connected: true,
    charging: true, power_w: 11050, current_a: [16, 16, 16], active_phases: 3 } as NativeWallbox["reading"],
    simulation: { state: "no_surplus", current_a: null, phases: null, power_w: 0 } };
  api.override("/api/wallboxes", { ...base, wallboxes: [charging] });
  await page.reload();
  await expect(card).toContainText("lädt mit 11,05 kW");
  await expect(card.locator(".facts")).toContainText("16,0 / 16,0 / 16,0 A");
  await expect(card.locator(".facts")).toContainText("OpenAmpere würde jetzt nicht laden: Es gibt zu wenig Solarüberschuss.");

  const unplugged: NativeWallbox = { ...planned, reading: { ...fox.reading, state: "idle", connected: false } as
    NativeWallbox["reading"], simulation: { state: "no_car", current_a: null, phases: null, power_w: 0 } };
  api.override("/api/wallboxes", { ...base, wallboxes: [unplugged] });
  await page.reload();
  await expect(card).toContainText("Die Simulation rechnet, sobald du ein Auto einsteckst.");
  await expect(card).not.toContainText("stoppt OpenAmpere das Laden");  // the hint of a wallbox it really controls

  const away: NativeWallbox = { ...fox, online: false, offline_since: Math.floor(Date.now() / 1000) - 600,
    reading: null, error: "Die Wallbox unter 192.168.178.61 antwortet nicht." };
  api.override("/api/wallboxes", { ...base, wallboxes: [away] });
  await page.reload();
  await expect(card).toContainText("nicht erreichbar seit");
  await expect(card).toContainText("bisherige Smartbox");
  await expect(card.locator(".facts")).toHaveCount(0);  // no old values
});

test("the demo's FoxESS charger can be switched to a simulated mode, the other wallbox cannot", async () => {
  const garage = (view: WallboxesView) => view.wallboxes.find((w) => w.id === "demo0003");
  try {
    const changed = await demoRequest<WallboxesView>("POST", "/api/wallboxes/demo0003/mode", { mode: "now" });
    expect(garage(changed)).toMatchObject({ mode: "now", simulated: true,
      simulation: { state: "charging", current_a: 16, phases: 3, power_w: 11040 } });
    expect(garage(await view())?.mode).toBe("now");
    await expect(demoRequest("POST", "/api/wallboxes/demo0001/mode", { mode: "now" })).rejects.toThrow("nur die Demo");
  } finally {
    await demoRequest("POST", "/api/wallboxes/demo0003/mode", { mode: "manual" });
  }
  expect(garage(await view())?.simulation).toBeNull();
});

test("a FoxESS charger is added with port and unit id; the limit and the waiting time are for its simulation", async ({ page, api }) => {
  const base = await view();
  api.override("/api/wallboxes/test", { reading: { ...READING, state: "idle", connected: false, charging: false,
    power_w: 0, set_current_a: null, max_current_a: 32, firmware: "1.16" } });
  await open(page, "more/wallbox");
  await page.getByRole("button", { name: "direkt", exact: true }).click();
  await page.getByRole("button", { name: "Wallbox hinzufügen oder prüfen" }).click();
  await page.getByLabel("Welche Wallbox hast du?").selectOption("foxess_ac");
  await expect(page.locator(".notice", { hasText: "liest die Wallbox nur" })).toContainText("bisherige Smartbox");
  await expect(page.getByLabel("Höchster Ladestrom (A)")).toHaveValue("16");
  await expect(page.locator(".field", { hasText: "Höchster Ladestrom (A)" })).toContainText("in der Simulation");
  await expect(page.getByLabel("Solar-Laden pausieren nach (Minuten)")).toHaveValue("10");
  await expect(page.getByLabel("Name")).toHaveValue("Wallbox");
  await page.getByLabel("IP-Adresse der Wallbox").fill("192.168.178.61");
  await page.getByText("Erweitert").click();
  await page.getByLabel("Port").fill("1502");
  const tested = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/test"));
  await page.getByRole("button", { name: "Verbindung prüfen" }).click();
  expect((await tested).postDataJSON()).toEqual({ model: "foxess_ac", host: "192.168.178.61", port: 1502, unit: 1, id: null });
  await expect(page.locator(".notice.ok")).toContainText("höchstens 32 A");
  expect(await layoutProblems(page)).toEqual([]);
  await page.getByLabel("Geräteadresse (Unit-ID)").fill("x");
  await page.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByText("Bitte Port und Geräteadresse als Zahlen angeben.")).toBeVisible();
  await page.getByLabel("Geräteadresse (Unit-ID)").fill("1");
  api.override("/api/wallboxes", base);
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes") && r.method() === "PUT");
  await page.getByRole("button", { name: "Speichern" }).click();
  const item = (await sent).postDataJSON().wallboxes.at(-1);
  expect(item).toMatchObject({ model: "foxess_ac", host: "192.168.178.61", port: 1502, unit: 1, mode: "manual",
    local_key: "" });
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

// #464: a FoxESS charger that went "locked" is released with its work mode, after a question
test("a locked FoxESS charger can be released with its work mode", async ({ page, api }) => {
  const base = await view();
  const garage = base.wallboxes.find((w) => w.id === "demo0003") as NativeWallbox;
  const locked: NativeWallbox = { ...garage, reading: { ...(garage.reading as NonNullable<NativeWallbox["reading"]>),
    state: "unknown", connected: false, work_mode: "locked" } };
  api.override("/api/wallboxes", { ...base, wallboxes: [locked] });
  api.override("/api/wallboxes/demo0003/work-mode", { dry_run: false, work_mode: "controlled", ...base,
    wallboxes: [{ ...locked, reading: { ...locked.reading, work_mode: "controlled" } }] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Garage" });
  await expect(card).toContainText("Die Wallbox ist gesperrt und lädt nicht");
  await card.getByRole("group", { name: "Betriebsmodus der Wallbox" }).getByRole("button", { name: "Gesteuert" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Betriebsmodus auf „Gesteuert“ stellen?");
  await expect(dialog).toContainText("Smartbox");
  expect(await layoutProblems(page)).toEqual([]);
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/demo0003/work-mode"));
  await dialog.getByRole("button", { name: "Umstellen" }).click();
  expect((await sent).postDataJSON()).toEqual({ mode: "controlled" });
  await expect(page.getByText("Der Betriebsmodus der Wallbox ist geändert.")).toBeVisible();
  await expect(card).not.toContainText("Die Wallbox ist gesperrt");
});

// #465: real control of the FoxESS charger only after a question that says what happens if OpenAmpere fails
test("real control of a FoxESS charger is switched on after a question and off again", async ({ page, api }) => {
  const base = await view();
  const garage = base.wallboxes.find((w) => w.id === "demo0003") as NativeWallbox;
  const controlled: NativeWallbox = { ...garage, control: true, simulated: false, read_only: false, simulation: null };
  api.override("/api/wallboxes/demo0003/control", { ...base, wallboxes: [base.wallboxes[0], controlled] });
  await open(page, "devices");
  const card = page.locator(".card.wallbox", { hasText: "Garage" });
  await card.getByRole("button", { name: "Echte Steuerung einschalten (Test)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("mit 6 A weiter");
  await expect(dialog).toContainText("„Gesteuert“");
  expect(await layoutProblems(page)).toEqual([]);
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/demo0003/control"));
  await dialog.getByRole("button", { name: "Einschalten" }).click();
  expect((await sent).postDataJSON()).toEqual({ on: true });
  await expect(card).toContainText("Echte Steuerung (Test): OpenAmpere startet, stoppt und regelt diese Wallbox.");
  await expect(card.locator(".notice", { hasText: "Nur eine Simulation" })).toHaveCount(0);
  api.override("/api/wallboxes/demo0003/control", base);
  const off = page.waitForRequest((r) => r.url().endsWith("/api/wallboxes/demo0003/control"));
  await card.getByRole("button", { name: "Zurück zur Simulation" }).click();
  expect((await off).postDataJSON()).toEqual({ on: false });
  await expect(card.locator(".notice", { hasText: "Nur eine Simulation" })).toBeVisible();
});
