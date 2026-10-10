// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, open, test } from "./fixtures";

const day = (d: number) => new Date(2026, 9, d).getTime() / 1000;
const entry = (d: number, pv: number) => ({
  ts: day(d), pv, load: 12000, grid_import: 2000, grid_export: 0, battery_charge: 0, battery_discharge: 0, soc: null,
});

// the week chart cut off half of the outer bars (no generation on the first day, no consumption on the last)
// and did not start its y axis at zero
test("energy bars are complete and grow from zero", async ({ page, api }) => {
  api.override("/api/energy/timeline", { entries: [entry(5, 10000), entry(6, 20000), entry(7, 15000)] });
  await open(page, "report");
  await page.getByRole("button", { name: "Woche" }).click();
  const chart = page.getByRole("img", { name: /^Diagramm Energie/ });
  await chart.scrollIntoViewIfNeeded();
  await expect(chart.locator("canvas")).toBeVisible();

  const bars = await chart.evaluate((el) => {
    const canvas = el.querySelector("canvas")!;
    const over = el.querySelector(".u-over")!.getBoundingClientRect();
    const box = canvas.getBoundingClientRect();
    const scale = canvas.width / box.width;
    const ctx = canvas.getContext("2d")!;
    const style = getComputedStyle(document.documentElement);
    // the colour the canvas stores for a css variable
    const rgb = (name: string) => {
      const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
      probe.fillStyle = style.getPropertyValue(name).trim();
      probe.fillRect(0, 0, 1, 1);
      return [...probe.getImageData(0, 0, 1, 1).data.slice(0, 3)];
    };
    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const is = (x: number, y: number, c: number[]) => {
      const i = (y * canvas.width + x) * 4;
      return c.every((v, k) => Math.abs(pixels[i + k] - v) < 8);
    };
    const left = Math.round((over.left - box.left) * scale), right = Math.round((over.right - box.left) * scale);
    const top = Math.round((over.top - box.top) * scale), bottom = Math.round((over.bottom - box.top) * scale);
    // bars of one colour along a row just above the x axis: [start, end] in canvas pixels
    const runs = (c: number[]) => {
      const found: [number, number][] = [];
      const y = bottom - Math.round(3 * scale);
      for (let x = left; x < right; x++) {
        if (!is(x, y, c)) continue;
        const last = found[found.length - 1];
        if (last && last[1] === x - 1) last[1] = x; else found.push([x, x]);
      }
      return found;
    };
    const height = ([a, b]: [number, number]) => {
      const x = Math.round((a + b) / 2);
      let y = top;
      while (y < bottom && !is(x, y, rgb("--pv"))) y++;
      return (bottom - y) / (bottom - top);
    };
    const pv = runs(rgb("--pv"));
    return { pv: pv.length, consumption: runs(rgb("--house")).length, heights: pv.map(height) };
  });

  expect(bars.pv, "generation bars").toBe(3);
  expect(bars.consumption, "consumption bars").toBe(3);
  // 10 kWh is half of 20 kWh only on an axis that starts at zero
  expect(bars.heights[0] / bars.heights[1]).toBeCloseTo(0.5, 1);
});
