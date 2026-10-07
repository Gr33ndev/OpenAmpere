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
  { action: "export_limit", details: { from: { export_limit_w: 4000 }, to: { export_limit_w: 600 } }, dry_run: false, result: "ok" },
  { action: "battery_settings_check", details: { from: { min_soc_on_grid: 20 }, to: { min_soc_on_grid: 15 } }, dry_run: false,
    result: "von einem anderen Gerät überschrieben" },
  { action: "consumer", details: { from: { consumer: "Heizstab", on: false }, to: { consumer: "Heizstab", on: true } }, dry_run: true,
    result: "nicht geschaltet (Testmodus): Überschuss 2600 W" },
  { action: "something_new", details: "not an object", dry_run: false, result: "Fehler: keine Antwort" },
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
    "Sonstige Änderung (something_new).",
  ]) await expect(page.getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByText("automatisch in der Nacht", { exact: false })).toBeVisible();
  await expect(page.getByText("Testmodus – nichts geändert", { exact: false })).toBeVisible();
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
