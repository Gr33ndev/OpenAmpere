// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

const running = (phase: "work" | "soc", socCursor: string) => ({
  status: "running", phase, days_total: 400, work_done: phase === "soc" ? 400 : 120, soc_done: phase === "soc" ? 30 : 0,
  imported: 12000, start: "2025-09-01", end: "2026-10-06", soc_cursor: socCursor, error: null, notice: null,
  eta_seconds: 3600, key_set: true,
});

// #476: owners did not know that the state of charge of old days comes in a second step
test("the import card says that the state of charge follows in step 2", async ({ page, api }) => {
  api.override("/api/import/cloud", running("work", "2026-10-06"));
  await open(page, "more/data");
  await expect(page.getByText("Schritt 1 von 2: Energiewerte")).toBeVisible();
  await expect(page.getByText("Danach holt OpenAmpere in Schritt 2 den Ladestand des Speichers")).toBeVisible();

  api.override("/api/import/cloud", running("soc", "2026-09-05"));
  await page.reload();
  await expect(page.getByText("Schritt 2 von 2: Ladestand des Speichers")).toBeVisible();
  await expect(page.getByText("Der Ladestand erscheint in der Auswertung nach und nach")).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("a day whose state of charge is still being imported says so in the report", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-10-07T12:00:00") });
  const at = (h: number) => new Date(2026, 8, 1, h).getTime() / 1000;
  api.override("/api/power/timeline", { from: 0, to: 0, step: 300, entries: [
    { ts: at(10), pv: 1000, house: 400, grid: 0, battery: 600, soc: null },
    { ts: at(11), pv: 1200, house: 500, grid: 0, battery: 700, soc: null }] });
  api.override("/api/import/cloud", running("soc", "2026-09-05"));  // 1 September comes later
  await open(page, "report");
  await page.locator('input[type="date"]').first().fill("2026-09-01");
  await expect(page.getByText("Den Ladestand dieses Tages holt der laufende Import")).toBeVisible();

  api.override("/api/import/cloud", { ...running("soc", "2026-08-20"), soc_cursor: "2026-08-20" });  // already done
  await page.reload();
  await page.locator('input[type="date"]').first().fill("2026-09-01");
  await expect(page.getByText("Den Ladestand dieses Tages holt der laufende Import")).toHaveCount(0);
});
