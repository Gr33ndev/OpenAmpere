// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, PAGES, test } from "./fixtures";

const ARROWS = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight"];

test("the easter egg code on the keyboard switches the energy flow to the retro look and back", async ({ page }) => {
  await open(page, "");
  const flow = page.locator(".flow");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(flow).toHaveClass(/retro/);
  await expect(page.locator(".retro-banner")).toBeVisible();
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(flow).not.toHaveClass(/retro/);
});

test("the easter egg code can be tapped on the energy flow, B and A appear as buttons", async ({ page }) => {
  await open(page, "");
  const icon = (name: string) => page.locator(`.flow-node.${name} .flow-icon`);
  for (const name of ["sun", "sun", "house", "house", "battery", "grid", "battery", "grid"]) await icon(name).click();
  await page.locator(".retro-pad .b").click();
  await page.locator(".retro-pad .a").click();
  await expect(page.locator(".flow")).toHaveClass(/retro/);
  await expect(page.locator(".retro-pad")).toHaveCount(0);
});

test("a wrong key starts the code over", async ({ page }) => {
  await open(page, "");
  for (const key of ["ArrowUp", "ArrowUp", "ArrowLeft", ...ARROWS.slice(2), "b", "a"]) await page.keyboard.press(key);
  await expect(page.locator(".flow")).not.toHaveClass(/retro/);
});

// the pixel filter drew nothing in Safari when it was applied to the HTML icons
test("the icons stay visible in the retro look", async ({ page }) => {
  await open(page, "");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(page.locator(".flow")).toHaveClass(/retro/);
  for (const name of ["sun", "battery", "grid"]) {
    const icon = page.locator(`.flow-node.${name} .flow-icon`);
    const shown = await icon.screenshot({ animations: "disabled" });
    await icon.evaluate((el) => el.querySelectorAll("svg").forEach((svg) => { svg.style.visibility = "hidden"; }));
    const hidden = await icon.screenshot({ animations: "disabled" });
    expect(shown.equals(hidden), `${name} icon is drawn`).toBe(false);
  }
});

test("the retro look covers the whole app, stays when switching pages and goes with the code again", async ({ page }) => {
  await open(page, "");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  const html = page.locator("html");
  await expect(html).toHaveClass(/retro/);
  await page.locator("nav.bottom button").nth(1).click();
  await expect(page.locator(".flow")).toHaveCount(0);
  await expect(html).toHaveClass(/retro/);
  await page.locator("nav.bottom button").nth(0).click();
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(html).not.toHaveClass(/retro/);
});

test("the navigation icons stay visible in the retro look", async ({ page }) => {
  await open(page, "");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(page.locator("html")).toHaveClass(/retro/);
  const icon = page.locator("nav.bottom button").nth(3);
  const shown = await icon.screenshot({ animations: "disabled" });
  await icon.evaluate((el) => { el.querySelector("svg")!.style.visibility = "hidden"; });
  const hidden = await icon.screenshot({ animations: "disabled" });
  expect(shown.equals(hidden), "navigation icon is drawn").toBe(false);
});

// the monospace font is wider: every page must still fit the screen in the retro look
for (const route of PAGES) {
  test(`page /${route} in the retro look`, async ({ page }, info) => {
    test.fixme(route === "more/tariff" && info.project.name === "iphone", "date field makes the page 11px wider in Playwright's WebKit only");
    await open(page, "");
    for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
    await expect(page.locator("html")).toHaveClass(/retro/);
    await page.evaluate((r) => { window.location.hash = `#/${r}`; }, route);
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).not.toBeEmpty();
    await expect(page.locator("html")).toHaveClass(/retro/);
    expect(await layoutProblems(page)).toEqual([]);
  });
}

/** Colour changes along one row of the chart canvas: a bar with pixel gaps has many more than a solid bar. */
async function colourChanges(page: import("@playwright/test").Page): Promise<number> {
  await page.getByRole("button", { name: "Woche", exact: true }).click();
  const canvas = page.locator(".chart canvas").first();
  await expect(canvas).toBeVisible();
  await page.waitForTimeout(300);
  return canvas.evaluate((el: HTMLCanvasElement) => {
    const ctx = el.getContext("2d")!;
    const row = ctx.getImageData(0, Math.round(el.height * 0.8), el.width, 1).data;
    let changes = 0;
    for (let i = 4; i < row.length; i += 4) {
      if (Math.abs(row[i] - row[i - 4]) + Math.abs(row[i + 1] - row[i - 3]) + Math.abs(row[i + 2] - row[i - 2]) + Math.abs(row[i + 3] - row[i - 1]) > 60) changes++;
    }
    return changes;
  });
}

test("the charts are drawn in pixels in the retro look", async ({ page }) => {
  await open(page, "report");
  const normal = await colourChanges(page);
  await open(page, "");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(page.locator("html")).toHaveClass(/retro/);
  await page.evaluate(() => { window.location.hash = "#/report"; });
  const retro = await colourChanges(page);
  expect(retro, `colour changes: normal ${normal}, retro ${retro}`).toBeGreaterThan(normal * 3);
});
