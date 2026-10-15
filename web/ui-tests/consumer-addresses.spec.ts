// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { DevicesView } from "../src/api";
import type { ConsumerData } from "../src/ConsumersPage";
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// a backup restored on another server: the stored switch addresses cannot be read, the device waits for new ones (#392)
const HINT = "schaltet OpenAmpere das Gerät nicht, auch nicht aus";

async function withLostDevice(api: { override(path: string, body: unknown): void }): Promise<ConsumerData> {
  const { consumers } = await demoRequest<{ consumers: ConsumerData[] }>("GET", "/api/consumers");
  const lost: ConsumerData = { ...consumers[0], id: "web1", name: "Pumpe", kind: "http", url_on: "", url_off: "",
    addresses_lost: true, state: undefined };
  api.override("/api/consumers", { consumers: [...consumers, lost] });
  return lost;
}

test("a device whose switch addresses cannot be read asks for them again and is saved with new ones", async ({ page, api }) => {
  const lost = await withLostDevice(api);
  await open(page, "more/device-setup");
  const row = page.locator(".device-row-compact", { hasText: "Pumpe" });
  await expect(row).toContainText("Schaltadressen neu eintragen");
  await row.click();
  await expect(page.getByText(HINT)).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  const save = page.getByRole("button", { name: "Speichern", exact: true });
  await expect(save).toBeDisabled();
  await page.getByLabel("Adresse zum Einschalten").fill("http://192.0.2.10/on");
  await page.getByLabel("Adresse zum Ausschalten").fill("http://192.0.2.10/off");

  const { consumers } = await demoRequest<{ consumers: ConsumerData[] }>("GET", "/api/consumers");
  api.override("/api/consumers", { consumers: [...consumers, { ...lost, url_on: "http://192.0.2.10/on",
    url_off: "http://192.0.2.10/off", addresses_lost: false }] });
  const sent = page.waitForRequest((r) => r.url().endsWith("/api/consumers") && r.method() === "PUT");
  await save.click();
  const pump = (await sent).postDataJSON().consumers[1];
  expect(pump).toMatchObject({ id: "web1", name: "Pumpe", url_on: "http://192.0.2.10/on", url_off: "http://192.0.2.10/off" });
  expect(pump).not.toHaveProperty("addresses_lost");
  await expect(page.locator(".device-row-compact", { hasText: "Pumpe" })).not.toContainText("Schaltadressen neu eintragen");
});

test("the devices tab says that the switch addresses must be entered again", async ({ page, api }) => {
  await withLostDevice(api);
  const view = await demoRequest<DevicesView>("GET", "/api/devices");
  api.override("/api/devices", { ...view, devices: [...view.devices, { key: "c:web1", id: "web1", source: "openampere",
    name: "Pumpe", kind: "switch", enabled: true, power_w: 0, active: false, on: null, temperature_c: null,
    target_c: null, status: null, error: null, override: null, connected: null, addresses_lost: true }] });
  await open(page, "devices");
  const card = page.locator(".card.device-card", { hasText: "Pumpe" });
  await expect(card.locator(".hint").first()).toHaveText("Schaltadressen neu eintragen");
  await expect(card.getByText(HINT)).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
  await card.getByRole("button", { name: "Schaltadressen neu eintragen" }).click();
  await expect(page).toHaveURL(/#\/more\/device-setup$/);
});
