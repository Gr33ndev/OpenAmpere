// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { test as base, expect, type Page } from "@playwright/test";
import { demoRequest, snapshot } from "../src/demo/server";

/** Every test gets the real app with the API answered by the demo simulation, and fails on any console error
 *  (also a 404 for an endpoint the demo does not know). `api.override` changes one answer, e.g. an update. */
/** Answers for endpoints the demo leaves out on purpose (DEMO_NOT_NEEDED) but the real app reads. */
const NOT_IN_DEMO: Record<string, unknown> = {
  "/api/setup/networks": { prefixes: ["192.168.178"] },
  "/api/setup/drivers": { drivers: [] },
};

export const test = base.extend<{ api: { override(path: string, body: unknown): void }; consoleErrors: string[] }>({
  consoleErrors: [async ({ page }, use) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`${message.text()} (${message.location().url})`);
    });
    page.on("pageerror", (error) => errors.push(String(error)));
    await use(errors);
    expect(errors, "errors in the browser console").toEqual([]);
  }, { auto: true }],
  api: [async ({ page }, use) => {
    const overrides = new Map<string, unknown>();
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (overrides.has(url.pathname)) return route.fulfill({ json: overrides.get(url.pathname) });
      if (url.pathname in NOT_IN_DEMO) return route.fulfill({ json: NOT_IN_DEMO[url.pathname] });
      if (route.request().method() !== "GET") return route.fulfill({ json: {} });
      try {
        return await route.fulfill({ json: await demoRequest("GET", url.pathname + url.search) });
      } catch (error) {
        return route.fulfill({ status: 404, json: { detail: String(error) } });
      }
    });
    await page.routeWebSocket("**/api/live/ws", (ws) => {
      const send = () => ws.send(JSON.stringify(snapshot()));
      send();
      const timer = setInterval(send, 2000);
      ws.onClose(() => clearInterval(timer));
    });
    await use({ override: (path, body) => overrides.set(path, body) });
  }, { auto: true }],
});

export { expect };

/** Every page of the app, as hash routes. */
export const PAGES = [
  "", "devices", "devices/battery", "devices/charging", "report", "more",
  ...["installation", "pv", "export-limit", "connection", "device-setup", "wallbox", "gridmeter", "control", "notify",
    "tariff", "billing", "remote", "apps", "security", "appearance", "data", "diagnostics", "about", "changelog", "licenses"]
    .map((page) => `more/${page}`),
];

export async function open(page: Page, route: string): Promise<void> {
  await page.goto(`/#/${route}`);
  await expect(page.locator("nav.bottom")).toBeVisible();
  await page.waitForLoadState("networkidle");
}

/** Layout rules every page must keep, checked in the browser. Returns what is broken, in words. */
export function layoutProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const name = (el: Element) => `${el.tagName.toLowerCase() + (el.className && typeof el.className === "string"
      ? `.${el.className.trim().split(/\s+/).join(".")}` : "")} "${(el.textContent ?? "").trim().slice(0, 30)}"`;
    const sticksOut = (el: Element) => el.getBoundingClientRect().right > vw + 1;
    // wider than the screen and not inside a scroll box or clipping box that fits on the screen
    const culprit = (el: Element) => {
      if (!sticksOut(el)) return false;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        if (getComputedStyle(p).overflowX !== "visible" && !sticksOut(p)) return false;
      }
      return true;
    };

    // nothing may stick out sideways: on a phone the whole page could then be dragged left and right
    if (document.documentElement.scrollWidth > vw + 1) {
      const culprits = [...document.querySelectorAll("body *")].filter(culprit);
      // the outermost ones: their parent still fits
      const wide = culprits.filter((el) => !el.parentElement || !culprits.includes(el.parentElement)).slice(0, 3).map(name);
      problems.push(`page is ${document.documentElement.scrollWidth}px wide on a ${vw}px screen: ${wide.join(", ")}`);
    }

    // the bottom navigation floats at the bottom of the screen, whatever was scrolled
    const nav = document.querySelector("nav.bottom");
    if (nav) {
      const rect = nav.getBoundingClientRect();
      const gap = parseFloat(getComputedStyle(nav).bottom);
      if (Math.abs(rect.bottom - (vh - gap)) > 2) {
        problems.push(`bottom navigation ends at ${Math.round(rect.bottom)}px instead of ${Math.round(vh - gap)}px`);
      }
      if (rect.left < 0 || rect.right > vw) problems.push("bottom navigation sticks out of the screen");
    }

    // the backdrop behind the iPhone status bar stays at the top
    const backdrop = document.querySelector(".status-bar-backdrop");
    if (backdrop && Math.abs(backdrop.getBoundingClientRect().top) > 1) {
      problems.push(`status bar backdrop moved to ${Math.round(backdrop.getBoundingClientRect().top)}px`);
    }
    return problems;
  });
}

/** Scrolls through the whole page, down and back up, and collects the layout problems seen on the way. */
export async function scrollProblems(page: Page): Promise<string[]> {
  const height = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
  const found = new Set<string>();
  const positions = [0.25, 0.5, 1, 0.75, 0.4, 0.1, 0].map((f) => Math.round(f * height));
  for (const y of positions) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(50);
    for (const problem of await layoutProblems(page)) found.add(`after scrolling to ${y}px: ${problem}`);
  }
  return [...found];
}
