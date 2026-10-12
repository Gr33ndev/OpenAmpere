// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Status } from "../src/api";
import { demoRequest } from "../src/demo/server";
import { expect, INGRESS, open, test } from "./fixtures";

const DIRECT_URL = "http://192.168.178.20:8780";

test("opened by IP before a password exists, the app says to set it in Home Assistant", async ({ page, api }) => {
  api.override("/api/auth/status", { configured: false, authenticated: false, home_assistant: "direct" });
  await page.goto("/#/");
  await expect(page.getByText("in der Seitenleiste öffnen")).toBeVisible();
  await expect(page.locator("input[type=password]")).toHaveCount(0); // nobody else in the network can choose it
});

test("in the sidebar the app needs no password, which is only for direct access", async ({ page, api }) => {
  api.override("/api/auth/status", { configured: false, authenticated: true, home_assistant: "ingress" });
  api.override("/api/status", { ...(await demoRequest<Status>("GET", "/api/status")), home_assistant: { direct_url: DIRECT_URL } });
  await open(page, "more/security");
  await expect(page.getByText("über Home Assistant angemeldet")).toBeVisible();
  await expect(page.locator("code", { hasText: DIRECT_URL })).toBeVisible();
  await expect(page.locator("input[type=password]")).toHaveCount(2); // new password and repeat, no old one
});

// #300: Home Assistant shows the app under its ingress address, so every request must stay below that address
test("works under the ingress address of the Home Assistant app", async ({ page, baseURL }) => {
  // the files of the web app below the ingress address, as the Supervisor passes them on without the prefix
  await page.route(`**${INGRESS}**`, async (route) => {
    const rest = new URL(route.request().url()).pathname.slice(INGRESS.length);
    if (rest.startsWith("api/")) return route.fallback();
    return route.fulfill({ response: await route.fetch({ url: `${baseURL}/${rest}` }) });
  });
  const outside: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (!pathname.startsWith(INGRESS)) outside.push(pathname);
  });

  await page.goto(`${INGRESS}#/more`);
  await expect(page.locator("nav.bottom")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("main")).not.toBeEmpty();
  await page.goto(`${INGRESS}#/devices/battery`);
  await expect(page.locator("main")).not.toBeEmpty();
  await page.waitForLoadState("networkidle");

  expect(outside, "requests outside the ingress address").toEqual([]);
});

test("in the sidebar, users of Home Assistant who are not admins can only view", async ({ page, api }) => {
  api.override("/api/auth/status", { configured: true, authenticated: false, home_assistant: "ingress" });
  await open(page, "more/security");
  await expect(page.getByText("kein Administrator")).toBeVisible();
  await expect(page.locator("input[type=password]")).toHaveCount(0); // no password form for them
});
