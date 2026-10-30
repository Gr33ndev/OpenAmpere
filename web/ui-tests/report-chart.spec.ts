// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Locator } from "@playwright/test";
import { expect, layoutProblems, open, test } from "./fixtures";

const day = (d: number) => new Date(2026, 9, d).getTime() / 1000;
const entry = (d: number, pv: number) => ({
  ts: day(d), pv, load: 12000, grid_import: 2000, grid_export: 0, battery_charge: 0, battery_discharge: 0, soc: null,
});

/** How many bars of a color the chart draws, asked again until it holds: a click on a period or the legend draws the
 *  canvas a moment later, slower on CI than locally. */
const drawn = (chart: Locator, color: string, message: string, count: number) =>
  expect.poll(async () => (await bars(chart, color)).length, { message }).toBe(count);

/** Bars of one colour (a css variable or a colour) along a row just above the x axis: their start and end as a share
 *  of the plot width, and their height as a share of the plot height. */
async function bars(chart: Locator, color: string) {
  await chart.scrollIntoViewIfNeeded();
  await expect(chart.locator("canvas")).toBeVisible();
  return chart.evaluate((el, color) => {
    const canvas = el.querySelector("canvas")!;
    const over = el.querySelector(".u-over")!.getBoundingClientRect();
    const box = canvas.getBoundingClientRect();
    const scale = canvas.width / box.width;
    const ctx = canvas.getContext("2d")!;
    // the colour the canvas stores for a css variable or colour
    const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
    probe.fillStyle = color.startsWith("--") ? getComputedStyle(document.documentElement).getPropertyValue(color).trim() : color;
    probe.fillRect(0, 0, 1, 1);
    const rgb = [...probe.getImageData(0, 0, 1, 1).data.slice(0, 3)];
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const is = (x: number, y: number) => {
      const i = (y * canvas.width + x) * 4;
      return rgb.every((v, k) => Math.abs(pixels[i + k] - v) < 8);
    };
    const left = Math.round((over.left - box.left) * scale), right = Math.round((over.right - box.left) * scale);
    const top = Math.round((over.top - box.top) * scale), bottom = Math.round((over.bottom - box.top) * scale);
    const found: [number, number][] = [];
    const y = bottom - Math.round(3 * scale);
    for (let x = left; x < right; x++) {
      if (!is(x, y)) continue;
      const last = found[found.length - 1];
      if (last && last[1] === x - 1) last[1] = x; else found.push([x, x]);
    }
    return found.map(([a, b]) => {
      let y = top;
      while (y < bottom && !is(Math.round((a + b) / 2), y)) y++;
      return { start: (a - left) / (right - left), end: (b - left) / (right - left), height: (bottom - y) / (bottom - top) };
    });
  }, color);
}

// the week chart cut off half of the outer bars (no generation on the first day, no consumption on the last)
// and did not start its y axis at zero
test("energy bars are complete and grow from zero", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-10-07T12:00:00") }); // the week of the entries
  api.override("/api/energy/timeline", { entries: [entry(5, 10000), entry(6, 20000), entry(7, 15000)] });
  await open(page, "report");
  await page.getByRole("button", { name: "Woche" }).click();
  const chart = page.getByRole("img", { name: /^Diagramm Energie/ });

  await drawn(chart, "--pv", "generation bars", 3);
  await drawn(chart, "--house", "consumption bars", 3);
  const pv = await bars(chart, "--pv");
  // 10 kWh is half of 20 kWh only on an axis that starts at zero
  expect(pv[0].height / pv[1].height).toBeCloseTo(0.5, 1);
});

// a year with data only in its first two months showed them across the whole width, with "Jan" several times on the
// axis instead of the twelve months (#403)
test("a year with little data still shows all its months", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-03-10T12:00:00") });
  const month = (m: number) => Date.UTC(2026, m - 1, 1, 12) / 1000;
  api.override("/api/energy/timeline", { entries: [{ ...entry(1, 300000), ts: month(1) }, { ...entry(1, 400000), ts: month(2) }] });
  api.override("/api/pv/inputs", { mode: "energy", inputs: [1, 2], labels: ["Süd", "West"],
    entries: [{ ts: month(1), values: [200000, 100000] }, { ts: month(2), values: [250000, 150000] }] });
  await open(page, "report");
  await page.getByRole("button", { name: "Jahr", exact: true }).click();

  const mainChart = page.getByRole("img", { name: /^Diagramm Energie/ });
  const inputsChart = page.locator(".section-title", { hasText: "Nach Modulfeldern" })
    .locator("xpath=following-sibling::div[contains(@class, 'chart')][1]");
  await drawn(mainChart, "--pv", "main chart", 2);
  await drawn(inputsChart, "#eec91d", "inputs chart", 2);
  const main = await bars(mainChart, "--pv");
  const inputs = await bars(inputsChart, "#eec91d");
  for (const [name, found] of [["main chart", main], ["inputs chart", inputs]] as const) {
    expect(found.length, name).toBe(2);
    // January and February are the first two of twelve months: both in the left sixth of the plot
    expect(found[1].end, name).toBeLessThan(0.2);
  }
});

// a tap on a legend entry hides or shows its line; the choice stays for the next visit (#400)
test("lines are hidden and shown with the legend and stay so after a reload", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-10-07T12:00:00") });
  api.override("/api/energy/timeline", { entries: [entry(5, 10000), entry(6, 20000), entry(7, 15000)] });
  await open(page, "report");
  await page.getByRole("button", { name: "Woche" }).click();
  const chart = page.getByRole("img", { name: /^Diagramm Energie/ });
  const legend = page.locator(".legend").first();
  const entryButton = (name: string) => legend.getByRole("button", { name });
  await drawn(chart, "--pv", "generation bars", 3);

  // without the self-supplied part the blue bar is only what came from the grid: 2 kWh next to 20 kWh generated
  await entryButton("Selbst versorgt").click();
  await expect(entryButton("Selbst versorgt")).toHaveAttribute("aria-pressed", "false");
  await drawn(chart, "--house", "self-supplied hidden", 0);
  const grid = await bars(chart, "--grid"), pv = await bars(chart, "--pv");
  expect(grid.length).toBe(3);
  expect(grid[1].height / pv[1].height).toBeCloseTo(0.1, 1);

  await entryButton("Erzeugt").click();
  await drawn(chart, "--pv", "generation hidden", 0);
  await drawn(chart, "--grid", "grid still shown", 3);

  // the last line cannot be hidden
  await entryButton("Aus dem Netz").click();
  await expect(entryButton("Aus dem Netz")).toHaveAttribute("aria-pressed", "true");

  await page.reload();
  await page.getByRole("button", { name: "Woche" }).click();
  await expect(entryButton("Erzeugt")).toHaveAttribute("aria-pressed", "false");
  await drawn(chart, "--pv", "still hidden after a reload", 0);
  await entryButton("Erzeugt").click();
  await drawn(chart, "--pv", "shown again", 3);
  expect(await layoutProblems(page)).toEqual([]);
});

test("a PV input is hidden with the legend of its chart", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-03-10T12:00:00") });
  const month = (m: number) => Date.UTC(2026, m - 1, 1, 12) / 1000;
  api.override("/api/pv/inputs", { mode: "energy", inputs: [1, 2], labels: ["Süd", "West"], totals_wh: [450000, 250000],
    entries: [{ ts: month(1), values: [200000, 100000] }, { ts: month(2), values: [250000, 150000] }] });
  await open(page, "report");
  await page.getByRole("button", { name: "Jahr", exact: true }).click();
  const title = page.locator(".section-title", { hasText: "Nach Modulfeldern" });
  const chart = title.locator("xpath=following-sibling::div[contains(@class, 'chart')][1]");
  const legend = title.locator("xpath=following-sibling::div[contains(@class, 'legend')][1]");
  await drawn(chart, "#eec91d", "Süd shown", 2);
  await legend.getByRole("button", { name: /Süd/ }).click();
  await drawn(chart, "#eec91d", "Süd hidden", 0);
  await drawn(chart, "#a78bfa", "West now from the axis", 2);
});

// #468: a day imported from the cloud has no local readings; the "Leistung" view draws its state of charge
test("the power view of an imported day shows the state of charge", async ({ page, api }) => {
  await page.clock.install({ time: new Date("2026-10-07T12:00:00") });
  const quarter = (h: number, soc: number) => ({ ts: new Date(2026, 9, 7, h).getTime() / 1000, pv: null, house: null,
    grid: null, battery: null, soc });
  api.override("/api/power/timeline", { from: 0, to: 0, step: 300, entries: [quarter(8, 40), quarter(9, 45), quarter(10, 52)] });
  await open(page, "report");
  await expect(page.locator(".legend").first().getByRole("button", { name: "Ladestand (rechte Achse)" })).toBeVisible();
  await expect(page.getByText("Für diesen Tag gibt es keine Leistungswerte, nur den Ladestand der Viertelstunden.",
    { exact: false })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});
