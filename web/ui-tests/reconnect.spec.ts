// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #640: after the inverter was gone, "Connect now" tries again at once instead of after the waiting time
async function unreachable(api: { override(path: string, body: unknown): void }): Promise<Status> {
  const status = await demoRequest<Status>("GET", "/api/status");
  const gone = { ...status, connected: false, stale: true, last_error: "Wechselrichter nicht erreichbar" };
  api.override("/api/status", gone);
  return gone;
}

function reconnects(page: import("@playwright/test").Page): string[] {
  const posted: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/api/inverter/reconnect")) posted.push(new URL(request.url()).search);
  });
  return posted;
}

test("the overview offers to connect again while the inverter is not reachable", async ({ page, api }) => {
  await unreachable(api);
  const posted = reconnects(page);
  await open(page, "");
  const notice = page.locator(".notice", { hasText: "jede Minute" });
  await expect(notice).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  await notice.getByRole("button", { name: "Jetzt verbinden" }).click();
  await expect.poll(() => posted).toEqual([""]);
  await expect(page.getByText("verbindet sich neu")).toBeVisible();
});

test("no button while the inverter is connected", async ({ page }) => {
  await open(page, "");
  await expect(page.locator(".page-head")).toBeVisible();
  await expect(page.getByRole("button", { name: "Jetzt verbinden" })).toHaveCount(0);
});

test("no button while another device answers at the address", async ({ page, api }) => {
  const status = await unreachable(api);
  api.override("/api/status", { ...status, wrong_device: { since: Date.now() / 1000 - 600, found: "60BH…02", expected: "60BH…01" } });
  await open(page, "");
  await expect(page.locator(".notice", { hasText: "anderes Gerät" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Jetzt verbinden" })).toHaveCount(0);
});

test("the connection page offers it and points to the notification", async ({ page, api }) => {
  await unreachable(api);
  const posted = reconnects(page);
  await open(page, "more/connection");
  const notice = page.locator(".notice", { hasText: "Nicht verbunden" });
  await expect(notice).toContainText("Mehr → Benachrichtigungen");
  expect(await layoutProblems(page)).toEqual([]);
  await notice.getByRole("button", { name: "Jetzt verbinden" }).click();
  await expect.poll(() => posted).toEqual([""]);
});

test("a further inverter that is not reachable gets its own button", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  const inverters = status.inverters ?? [];
  api.override("/api/status", { ...status, inverters: [inverters[0],
    { ...inverters[1], connected: false, last_error: "Wechselrichter nicht erreichbar" }] });
  const posted = reconnects(page);
  await open(page, "more/connection");
  const card = page.getByTestId(`inverter-${inverters[1].id}`);
  await card.getByRole("button", { name: "Jetzt verbinden" }).click();
  await expect.poll(() => posted).toEqual([`?inverter=${inverters[1].id}`]);
});
