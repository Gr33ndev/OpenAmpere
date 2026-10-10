// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { readFileSync } from "node:fs";
import { demoRequest } from "../src/demo/server";
import type { EvccView } from "../src/WallboxPage";
import { expect, open, test } from "./fixtures";

// #244: follow-ups to #221 and #223

test("a wallbox plan for after midnight on the day the clocks go back is for the next day", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-25T00:20:00+02:00") }); // a day with 25 hours in Germany
  const view = await demoRequest<EvccView>("GET", "/api/evcc");
  const lp = { ...view.state!.loadpoints[0], connected: true };
  const connected = { ...view, state: { ...view.state!, loadpoints: [lp] } };
  const sent: { action: string; value: { time: number } }[] = [];
  await page.route((url) => url.pathname === "/api/evcc", (route) => route.fulfill({ json: connected }));
  await page.route((url) => url.pathname === `/api/evcc/loadpoints/${lp.id}`, (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ json: connected });
  });
  await open(page, "devices");
  await page.getByRole("button", { name: "Bis zu einer Uhrzeit laden" }).click();
  await page.locator(".plan-form input[type=time]").fill("00:30");
  await page.getByRole("button", { name: "Ladeplan setzen" }).click();
  await expect.poll(() => sent.length).toBe(1);
  expect(new Date(sent[0].value.time * 1000).toISOString()).toBe("2026-10-25T23:30:00.000Z"); // 26.10., 00:30
});

test("a price typed in another format is not an unsaved change", async ({ page }) => {
  await open(page, "more/tariff");
  const price = page.locator("label.field", { hasText: "Strompreis (brutto)" }).locator("input").first();
  await expect(price).toHaveValue("35,00");
  await price.fill("35");
  await expect(page.locator(".unsaved")).toHaveCount(0);
  const kwp = page.locator("label.field", { hasText: "Modulleistung" }).locator("input");
  await kwp.fill("9,80");
  await expect(page.locator(".card", { hasText: "Modulleistung" }).getByRole("button", { name: "Speichern" })).toHaveCount(0);
});

test("the battery page waits for the values read back after a change", async ({ page, api }) => {
  api.override("/api/status", { ...(await demoRequest<object>("GET", "/api/status")),
    control: { enabled: true, dry_run: false } });
  await open(page, "devices/battery");
  let reads = 0;
  await page.route((url) => url.pathname === "/api/battery/settings", async (route) => {
    if (route.request().method() !== "GET") return route.fulfill({ json: { dry_run: false, written: {}, result: "ok" } });
    reads++;
    await new Promise((resolve) => setTimeout(resolve, 1500)); // the inverter takes a moment
    return route.fulfill({ json: { ...(await demoRequest<object>("GET", "/api/battery/settings")), max_soc: 95 } });
  });
  const slider = page.locator(".slider input[type=range]").nth(1);
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  const apply = page.getByRole("button", { name: "Übernehmen" });
  await apply.evaluate((button) => {
    (button as HTMLButtonElement).click();
    // checked after each change of the page: from the render of the click on, "not saved yet" must not show
    const seen = window as unknown as { unsaved: boolean };
    seen.unsaved = false;
    new MutationObserver(() => {
      if (document.querySelector(".unsaved")) seen.unsaved = true;
    }).observe(document.body, { childList: true, subtree: true });
  });
  await expect.poll(() => reads).toBe(1);
  await expect(apply).toBeDisabled();
  await page.waitForTimeout(1800); // until the read-back is shown
  expect(await page.evaluate(() => (window as unknown as { unsaved: boolean }).unsaved)).toBe(false);
  await expect(apply).toBeDisabled(); // the values read back are the form again
});

test("the retro look does not need light-dark(), which older browsers do not know", async ({ page }) => {
  expect(readFileSync("src/styles.css", "utf8")).not.toContain("light-dark(");
  await open(page, "more/appearance");
  await page.evaluate(() => {
    document.documentElement.classList.add("retro");
    document.querySelector("main")!.insertAdjacentHTML("beforeend", '<button type="button" class="btn primary" id="probe">A</button>');
  });
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    const colors = await page.locator("#probe").evaluate((el) => {
      const style = getComputedStyle(el);
      return [style.backgroundColor, style.color];
    });
    expect(colors).toEqual(theme === "light" ? ["rgb(48, 98, 48)", "rgb(255, 255, 255)"] : ["rgb(155, 188, 15)", "rgb(17, 17, 17)"]);
  }
});
