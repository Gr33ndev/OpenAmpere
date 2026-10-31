// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, open, test } from "./fixtures";

const SITE = "https://gr33ndev.github.io/OpenAmpere/";

// #512: the device knowledge of docs/devices/ is on the website, linked from the app
test("the more page links to the device knowledge", async ({ page }) => {
  await open(page, "more");
  await expect(page.getByRole("link", { name: /Gerätewissen/ })).toHaveAttribute("href", `${SITE}docs/devices/`);
});

test("my system links to the page about the inverter", async ({ page }) => {
  await open(page, "more/installation");
  await expect(page.getByRole("link", { name: /Was wir über diesen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/foxess/h3.html`);
});

test("a SAJ inverter gets its own page", async ({ page, api }) => {
  const status = (await demoRequest("GET", "/api/status")) as { device: Record<string, unknown> };
  api.override("/api/status", { ...status, device: { ...status.device, manufacturer: "SAJ", driver: "saj", register_map: "saj_h2" } });
  await open(page, "more/installation");
  await expect(page.getByRole("link", { name: /Was wir über diesen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/saj/h2-family.html`);
});

test("a Solarmax gets its own page", async ({ page, api }) => {
  const status = (await demoRequest("GET", "/api/status")) as { device: Record<string, unknown> };
  api.override("/api/status", {
    ...status,
    device: { ...status.device, manufacturer: "Solarmax", driver: "solarmax", register_map: "solarmax_max_storage" },
  });
  await open(page, "more/installation");
  await expect(page.getByRole("link", { name: /Was wir über diesen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/solarmax/max-storage.html`);
});

test("a Senec gets its own page", async ({ page, api }) => {
  const status = (await demoRequest("GET", "/api/status")) as { device: Record<string, unknown> };
  api.override("/api/status", { ...status, device: { ...status.device, manufacturer: "Senec", driver: "senec", register_map: "senec_lala" } });
  await open(page, "more/installation");
  await expect(page.getByRole("link", { name: /Was wir über diesen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/senec/home-v3.html`);
});

test("an M-TEC gets its own page", async ({ page, api }) => {
  const status = (await demoRequest("GET", "/api/status")) as { device: Record<string, unknown> };
  api.override("/api/status", { ...status, device: { ...status.device, manufacturer: "M-TEC", driver: "mtec", register_map: "mtec_gen3 (socket)" } });
  await open(page, "more/installation");
  await expect(page.getByRole("link", { name: /Was wir über diesen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/mtec/energy-butler.html`);
});

test("the diagnostics page links to the page about the inverter", async ({ page }) => {
  await open(page, "more/diagnostics");
  await expect(page.getByRole("link", { name: /Was wir über deinen Wechselrichter wissen/ }))
    .toHaveAttribute("href", `${SITE}docs/devices/foxess/h3.html`);
});
