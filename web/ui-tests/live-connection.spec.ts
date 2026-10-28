// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

// #460: behind a reverse proxy without WebSocket support the overview said "Verbinde …" forever
test("a live connection that never opens is explained and the values are polled", async ({ page }) => {
  await page.routeWebSocket("**/api/live/ws", (ws) => ws.close({ code: 1008 }));
  await open(page, "dashboard");
  const hint = page.locator(".notice", { hasText: "Die Live-Verbindung kommt nicht zustande" });
  await expect(hint).toBeVisible({ timeout: 20_000 });
  await expect(hint.getByRole("link", { name: "Anleitung für Reverse-Proxys" })).toHaveAttribute("href", /reverse-proxy/);
  await expect(page.getByRole("status")).not.toHaveText("Verbinde …");  // the values come from the poll
  expect(await layoutProblems(page)).toEqual([]);
});

test("a working live connection shows no hint", async ({ page }) => {
  await open(page, "dashboard");
  await expect(page.getByRole("status")).not.toHaveText("Verbinde …");
  await expect(page.locator(".notice", { hasText: "Live-Verbindung" })).toHaveCount(0);
});
