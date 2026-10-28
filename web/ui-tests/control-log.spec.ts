// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { readFile } from "node:fs/promises";
import { expect, layoutProblems, open, test } from "./fixtures";

const NOW = Date.now() / 1000;

/** One entry per kind, in the shapes the server writes now and wrote before #153 (some of them broke the page). */
const ENTRIES = [
  { action: "grid_charging_switch", details: { from: { enabled: true }, to: { enabled: false } }, dry_run: false, result: "ok" },
  { action: "update", details: { from: "0.13.2", to: "0.13.3" }, dry_run: false, result: "manual" },
  { action: "update", details: { from: { version: "0.13.3" }, to: { version: "0.14.0" }, by: "auto" }, dry_run: false, result: "ok" },
  { action: "token_created", details: { name: "Home Assistant", scope: "control" }, dry_run: false, result: "ok" },
  { action: "token_revoked", details: { name: "Home Assistant" }, dry_run: false, result: "ok" },
  { action: "grid_charging", details: { source: "Home Assistant (Zugang für Apps)", to: { enabled: true, target_soc: 80 } },
    dry_run: false, result: "ok" },
  { action: "device_mode", details: { source: "Home Assistant (Zugang für Apps)", device: "heizstab", to: { mode: "boost", hours: 2 } },
    dry_run: false, result: "ok" },
  { action: "export_limit", details: { from: { export_limit_w: 4000 }, to: { export_limit_w: 600 }, source: "Admin (Home Assistant)" },
    dry_run: false, result: "ok" },
  { action: "battery_settings_check", details: { from: { min_soc_on_grid: 20 }, to: { min_soc_on_grid: 15 } }, dry_run: false,
    result: "von einem anderen Gerät überschrieben" },
  { action: "consumer", details: { from: { consumer: "Heizstab", on: false }, to: { consumer: "Heizstab", on: true } }, dry_run: true,
    result: "nicht geschaltet (Testmodus): Überschuss 2600 W", note: "Überschuss 2600 W" },
  // written by 0.23.1 (#315), still in the logs after the rollback (#320)
  { action: "grid_charging_reserve", details: { from: { min_soc_on_grid: 20 }, to: { min_soc_on_grid: 61 } }, dry_run: false, result: "ok" },
  { action: "grid_charging_reserve", details: { from: { min_soc_on_grid: 61 }, to: { min_soc_on_grid: 20 } }, dry_run: false, result: "ok" },
  { action: "grid_charging_reserve", details: { from: { min_soc_on_grid: 61 }, to: { min_soc_on_grid: 35 }, kept: true }, dry_run: false,
    result: "nicht zurückgestellt: die Notstrom-Reserve wurde inzwischen auf 35 % geändert" },
  // #322: changes of the grid charging settings, with the state of charge at the time
  { action: "grid_charging_settings", details: { from: { target_soc: 45, mode: "cheapest", max_price_ct: null },
    to: { target_soc: 35, mode: "window", max_price_ct: 25.5 }, soc: 30 }, dry_run: false, result: "ok" },
  // #351: found at a new address by its serial number
  { action: "inverter_relocated", details: { from: { host: "192.0.2.10" }, to: { host: "192.0.2.20" } }, dry_run: false, result: "ok" },
  // #350: every restored backup, with the switches it changed
  { action: "control_switches", details: { from: {}, to: {} }, dry_run: false, result: "Datensicherung wiederhergestellt" },
  { action: "control_switches", details: { from: { "control.enabled": false }, to: { "control.enabled": true } }, dry_run: false,
    result: "Datensicherung wiederhergestellt" },
  { action: "something_new", details: "not an object", dry_run: false, result: "Fehler: keine Antwort", note: "Fehler: keine Antwort" },
].map((e, i) => ({ ts: NOW - i * 600, ...e }));

test("control log entries read as sentences and can be exported", async ({ page, api }) => {
  api.override("/api/control/log", { entries: ENTRIES });
  await open(page, "more/control");
  const log = page.locator(".log-row");
  await expect(log).toHaveCount(ENTRIES.length);

  for (const text of [
    "Laden aus dem Netz ausgeschaltet.",
    "Update auf Version 0.13.3 gestartet (bisher 0.13.2).",
    "Update auf Version 0.14.0 gestartet (bisher 0.13.3).",
    "Zugang für Apps „Home Assistant“ angelegt. Er darf auch steuern.",
    "Zugang für Apps „Home Assistant“ entfernt.",
    "Laden aus dem Netz eingestellt: eingeschaltet und Ladeziel 80 %.",
    "heizstab für 2 Stunden eingeschaltet.",
    "Einspeisebegrenzung von 4.000 W auf 600 W geändert.",
    "Ein anderes Gerät hat die Speicher-Einstellung geändert: Notstrom-Reserve jetzt 15 % statt 20 %.",
    "Heizstab eingeschaltet.",
    "Notstrom-Reserve für das Laden aus dem Netz vorübergehend von 20 % auf 61 % angehoben.",
    "Notstrom-Reserve nach dem Laden aus dem Netz wieder auf 20 % gestellt (vorübergehend 61 %).",
    "Notstrom-Reserve nach dem Laden aus dem Netz nicht zurückgestellt: Sie wurde inzwischen auf 35 % geändert, dieser Wert bleibt.",
    "Laden aus dem Netz geändert: Laden bis von 45 % auf 35 %, Lade-Art von Günstigste Zeit auf Festes Zeitfenster und Höchstpreis von keiner auf 25,50 ct/kWh. (Ladestand 30 %)",
    "Wechselrichter unter einer neuen Adresse im Heimnetz gefunden: 192.0.2.20 statt 192.0.2.10. OpenAmpere verbindet sich jetzt mit dieser Adresse.",
    "Datensicherung wiederhergestellt.",
    "Datensicherung wiederhergestellt. OpenAmpere darf den Wechselrichter jetzt steuern.",
    "Sonstige Änderung (something_new).",
  ]) await expect(page.getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByText("automatisch in der Nacht", { exact: false })).toBeVisible();
  await expect(page.getByText("Testmodus – nichts geändert", { exact: false })).toBeVisible();
  await expect(page.getByText("Home Assistant (Zugang für Apps)", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Admin (Home Assistant)", { exact: false })).toBeVisible(); // through its sidebar (#305)
  // the reason from the server, without repeating what the sentence says
  await expect(page.getByText("Überschuss 2600 W", { exact: true })).toBeVisible();
  await expect(page.getByText("Fehler: keine Antwort", { exact: true })).toBeVisible();
  // the update used to be listed character by character, the trigger as a raw word
  const visible = await page.locator(".log-row > div").allInnerTexts();
  expect(visible.join("\n")).not.toMatch(/\b0: 0|\bmanual\b|→/);
  expect(await layoutProblems(page)).toEqual([]);

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Protokoll exportieren (CSV)" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^openampere-protokoll-\d{4}-\d{2}-\d{2}\.csv$/);
  const csv = await readFile((await file.path())!, "utf8");
  expect(csv.split("\r\n")[0]).toBe('﻿"Datum";"Uhrzeit";"Ereignis";"Ausgelöst";"Ergebnis";"Hinweis";"Technische Details"');
  expect(csv).toContain('"Laden aus dem Netz ausgeschaltet.";"in der App";"Erledigt"');
  expect(csv.trim().split("\r\n")).toHaveLength(ENTRIES.length + 1);
});

// #440: the simulated control of the FoxESS charger, never written: its own sentences and status
test("simulated wallbox entries say what OpenAmpere would have done", async ({ page, api }) => {
  const simulated = (to: unknown, result: string, note: string) => ({ action: "wallbox", dry_run: true, result, note,
    details: { from: { loadpoint: "Garage" }, to, simulation: true } });
  const entries = [
    simulated({ action: "start", value: { current_a: 8, phases: 3 } }, "Simulation, nicht gesendet: Überschuss 6000 W",
      "Überschuss 6000 W"),
    simulated({ action: "current", value: { current_a: 13, phases: 1 } }, "Simulation, nicht gesendet: Überschuss 3000 W",
      "Überschuss 3000 W"),
    simulated({ action: "stop", value: false }, "Simulation, nicht gesendet: zu wenig Überschuss (500 W)",
      "zu wenig Überschuss (500 W)"),
    // changes of the current at most every 5 minutes, with the range since the entry before (review of #443)
    simulated({ action: "current", value: { current_a: 10, phases: 3, low_a: 8, high_a: 13, minutes: 5 } },
      "Simulation, nicht gesendet: Überschuss 7000 W", "Überschuss 7000 W"),
    simulated({ action: "current", value: { current_a: 6, phases: 1, low_a: 6, high_a: 16, minutes: 1 } },
      "Simulation, nicht gesendet: Überschuss 1400 W", "Überschuss 1400 W"),
    simulated({ action: "current", value: { current_a: 16, phases: 3, low_a: 16, high_a: 16, minutes: 2 } },
      "Simulation, nicht gesendet: Lademodus „Schnell“", "Lademodus „Schnell“"),
  ].map((e, i) => ({ ts: NOW - i * 600, ...e }));
  api.override("/api/control/log", { entries });
  await open(page, "more/control");
  for (const text of [
    "Garage: Simulation – OpenAmpere würde das Laden mit 8 A auf 3 Phasen starten.",
    "Garage: Simulation – OpenAmpere würde mit 13 A auf 1 Phase weiterladen.",
    "Garage: Simulation – OpenAmpere würde das Laden stoppen.",
    "zu wenig Überschuss (500 W)",
    "Garage: Simulation – OpenAmpere würde mit 10 A auf 3 Phasen weiterladen (in den letzten 5 Minuten zwischen 8 und 13 A).",
    "Garage: Simulation – OpenAmpere würde mit 6 A auf 1 Phase weiterladen (in der letzten Minute zwischen 6 und 16 A).",
    "Garage: Simulation – OpenAmpere würde mit 16 A auf 3 Phasen weiterladen.",  // one value: no range
  ]) await expect(page.getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByText("Simulation – nichts an die Wallbox geschrieben", { exact: false })).toHaveCount(6);
  await expect(page.getByText("Testmodus – nichts geändert", { exact: false })).toHaveCount(0);
  expect(await layoutProblems(page)).toEqual([]);
});

// #464: the work mode with the one before; a write that may not have reached the charger is a warning
test("work mode entries say the old and the new mode", async ({ page, api }) => {
  const entry = (result: string, before?: string) => ({ action: "wallbox", dry_run: false, result,
    details: { from: { loadpoint: "Garage", id: "abcd1234", ...(before ? { value: before } : {}) },
      to: { action: "work_mode", value: "controlled" } } });
  api.override("/api/control/log", { entries: [
    { ts: NOW, ...entry("ok", "locked") },
    { ts: NOW - 600, ...entry("unbestätigt: Die Wallbox hat auf die Einstellung nicht geantwortet.") },
  ] });
  await open(page, "more/control");
  await expect(page.getByText("Garage: Betriebsmodus von „Gesperrt“ auf „Gesteuert“ gestellt.", { exact: true })).toBeVisible();
  await expect(page.getByText("Garage: Betriebsmodus „Gesteuert“ eingestellt.", { exact: true })).toBeVisible();
  await expect(page.getByText("Einstellung geändert")).toHaveCount(0);
});
