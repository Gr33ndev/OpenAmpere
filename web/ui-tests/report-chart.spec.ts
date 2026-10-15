// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Locator } from "@playwright/test";
import { expect, open, test } from "./fixtures";

const day = (d: number) => new Date(2026, 9, d).getTime() / 1000;
const entry = (d: number, pv: number) => ({
  ts: day(d), pv, load: 12000, grid_import: 2000, grid_export: 0, battery_charge: 0, battery_discharge: 0, soc: null,
});

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

  const pv = await bars(chart, "--pv");
  expect(pv.length, "generation bars").toBe(3);
  expect((await bars(chart, "--house")).length, "consumption bars").toBe(3);
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

  const main = await bars(page.getByRole("img", { name: /^Diagramm Energie/ }), "--pv");
  const inputs = await bars(page.locator(".section-title", { hasText: "Nach Modulfeldern" })
    .locator("xpath=following-sibling::div[contains(@class, 'chart')][1]"), "#eec91d");
  for (const [name, found] of [["main chart", main], ["inputs chart", inputs]] as const) {
    expect(found.length, name).toBe(2);
    // January and February are the first two of twelve months: both in the left sixth of the plot
    expect(found[1].end, name).toBeLessThan(0.2);
  }
});
