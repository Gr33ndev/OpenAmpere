// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

// #567: on the tablet there is no Tailscale helper, remote access goes through the Tailscale app for Android
test("remote access on the tablet points to the Tailscale app", async ({ page, api }) => {
  api.override("/api/remote", { available: false, state: "unavailable", port: 8080, android: true });
  await open(page, "more/remote");
  const notice = page.locator(".notice", { hasText: "Tailscale" });
  await expect(notice).toContainText("Port 8080");
  await expect(notice.getByRole("link", { name: "der App Tailscale" })).toBeVisible();
  await expect(page.getByText("install.sh")).toHaveCount(0); // no command for a Linux server
  expect(await layoutProblems(page)).toEqual([]);
});

// the Android notice has its own link text: the steps for the phone on a connected server keep theirs
test("the steps for the phone on a connected server read as before", async ({ page, api }) => {
  api.override("/api/remote", { available: true, state: "connected", port: 8080, mode: "kernel",
    address: "http://openampere.example:8080", https: { state: "off" } });
  await open(page, "more/remote");
  await expect(page.locator(".steps-list li").first()).toHaveText("Die Tailscale-App installieren.");
});
