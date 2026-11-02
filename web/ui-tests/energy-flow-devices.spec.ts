// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Status } from "../src/api";
import { expect, open, test } from "./fixtures";

// #563: a car that starts charging shows at the wallbox at once, not in the house until the next status
test("the energy flow takes the devices' power from the live values", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  const items = status.devices.items.map((d) => ({ ...d, power_w: 0, active: false, on: false })); // read before the start
  api.override("/api/status", { ...status, devices: { ...status.devices, items } });
  api.live({ house_power: 12800, device_power: { "evcc:1": 10200, "c:demo1": 0 } });
  await open(page, "");
  await expect(page.locator(".flow-node.house")).toHaveText("2,60 kW");
  await expect(page.locator(".flow")).toHaveAttribute("aria-label", /Carport 10,20 kW/);
  // the device list stays with the status, whose power goes with its state (not "lädt mit 0,00 kW" after a stop)
  await expect(page.locator(".device-row-compact", { hasText: "Carport" })).not.toContainText("10,20 kW");
});
