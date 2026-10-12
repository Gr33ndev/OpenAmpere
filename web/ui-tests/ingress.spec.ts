// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, INGRESS, test } from "./fixtures";

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
