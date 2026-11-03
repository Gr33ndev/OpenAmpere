// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Status } from "../src/api";
import { demoRequest } from "../src/demo/server";
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

// the WebView of old tablets (Chrome 91 in the Android 11 emulator) has no HTMLMetaElement.media: the app started
// with an empty page there
test("the app starts in a browser without HTMLMetaElement.media", async ({ page }) => {
  await page.addInitScript(() => {
    Reflect.deleteProperty(HTMLMetaElement.prototype, "media");
  });
  await open(page, "");
  await expect(page.locator(".page-head")).toBeVisible();
});

// on the tablet the licenses page also lists what only the Android app ships (scripts/android_licenses.py)
test("the licenses page on the tablet lists the parts of the Android app", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, android: true });
  await open(page, "more/licenses");
  await expect(page.locator(".section-title", { hasText: "Nur in der Android-App" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Chaquopy/ })).toBeVisible();
});

test("the licenses page elsewhere has no Android part", async ({ page }) => {
  await open(page, "more/licenses");
  await expect(page.locator(".section-title").first()).toBeVisible();
  await expect(page.locator(".section-title", { hasText: "Nur in der Android-App" })).toHaveCount(0);
});

test("the license groups are titled in the language of the app", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, android: true });
  await page.addInitScript(() => localStorage.setItem("openampere.lang", "en"));
  await open(page, "more/licenses");
  await expect(page.locator(".section-title", { hasText: "Only in the Android app" })).toBeVisible();
  await expect(page.locator(".section-title", { hasText: "App in the browser" })).toBeVisible();
  await expect(page.locator(".section-title", { hasText: "Nur in der Android-App" })).toHaveCount(0);
});
