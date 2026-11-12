// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Settings, Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #619: more than one inverter in one installation (the demo has two: "Haus" and "Garage")
test("the energy flow shows each inverter with its own values", async ({ page }) => {
  await open(page, "");
  const flow = page.locator(".flow");
  await expect(flow.locator(".flow-node.sun")).toContainText("Haus");
  await expect(flow.locator(".flow-node.sun-more")).toContainText("Garage");
  await expect(flow.locator(".flow-node.battery-more")).toContainText("Garage");
  await expect(page.locator(".section-title", { hasText: /^Wechselrichter$/ })).toHaveCount(0);  // no list any more
  expect(await layoutProblems(page)).toEqual([]);
});

test("an inverter without current values is shown as such", async ({ page, api }) => {
  api.live({ inverters: [{ id: "main", name: "Haus", connected: true, pv_power: 1000, battery_power: 0, battery_soc: 50 },
    { id: "wr2", name: "Garage", connected: false, has_pv: true, has_battery: true }] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.battery-more")).toContainText("Garage");  // its battery, without values
  await expect(page.locator(".flow .flow-node.battery-more")).toContainText("–");
  await expect(page.getByText("zeigt die Übersicht keine Summe")).toBeVisible();
});

test("with one inverter there is one sun and battery and no choice of inverter", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, inverters: [] });
  api.live({ inverters: [] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.sun-more, .flow .flow-node.battery-more")).toHaveCount(0);
  await open(page, "devices/battery");
  await expect(page.getByRole("combobox", { name: "Wechselrichter" })).toHaveCount(0);
});

test("the battery settings of the chosen inverter are read and written", async ({ page }) => {
  const paths: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/battery/settings")) paths.push(`${request.method()} ${new URL(request.url()).search}`);
  });
  await open(page, "devices/battery");
  const picker = page.getByRole("combobox", { name: "Wechselrichter" });
  await expect(picker).toHaveValue("main");
  await expect(page.getByText("20 %").first()).toBeVisible();
  await picker.selectOption("wr2");
  await expect(page.getByText("25 %").first()).toBeVisible();
  expect(paths).toContain("GET ?inverter=wr2");
});

test("a further inverter at whose address another device answers can be taken over", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  const inverters = status.inverters ?? [];
  api.override("/api/status", { ...status, inverters: [inverters[0],
    { ...inverters[1], connected: false, wrong_device: { since: Date.now() / 1000 - 600, found: "HST…02", expected: "HST…01" } }] });
  let posted = "";
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/api/setup/accept-device")) posted = new URL(request.url()).search;
  });
  await open(page, "");
  const notice = page.locator(".notice", { hasText: "„Garage“" });
  await expect(notice).toContainText("HST…02");
  await notice.getByRole("button", { name: "Das ist mein neuer Wechselrichter" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Übernehmen" }).click();
  await expect.poll(() => posted).toBe("?inverter=wr2");
});

test("further inverters are listed in the settings, changed and removed", async ({ page }) => {
  const bodies: Settings["values"][] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().endsWith("/api/settings")) bodies.push(request.postDataJSON());
  });
  await open(page, "more/connection");
  const garage = page.getByTestId("inverter-wr2");
  await expect(garage).toContainText("garage-speicher.local");
  await expect(garage).toContainText("Verbunden mit SAJ");
  expect(await layoutProblems(page)).toEqual([]);

  await garage.getByRole("switch", { name: "Misst einen eigenen Netzanschluss" }).click();
  await garage.getByRole("button", { name: "Speichern" }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0].inverters).toEqual([expect.objectContaining({ id: "wr2", name: "Garage", own_grid: true })]);

  await page.reload();  // the test server answers a change with nothing: read the settings again
  await expect(garage).toBeVisible();
  await garage.getByRole("button", { name: "Wechselrichter entfernen" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Am Gerät selbst ändert sich nichts");
  await dialog.getByRole("button", { name: "Entfernen" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1].inverters).toEqual([]);
});

test("adding a further inverter starts with the search in the network", async ({ page }) => {
  await open(page, "more/connection");
  await page.getByRole("button", { name: "Weiteren Wechselrichter hinzufügen" }).click();
  await expect(page.getByText("Suche den weiteren Wechselrichter")).toBeVisible();
  await expect(page.getByRole("button", { name: "Hinzufügen" })).toBeDisabled();  // only after a successful test
});

test("a new further inverter is saved without a name and shown by its position", async ({ page }) => {
  const bodies: Settings["values"][] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().endsWith("/api/settings")) bodies.push(request.postDataJSON());
  });
  await page.route("**/api/setup/test", (route) => route.fulfill({ json: { ok: true, device: { manufacturer: "SAJ",
    model: "H2-8K-T2", serial: null, firmware: null, driver: "saj", unit: 1, supports_control: true } } }));
  await open(page, "more/connection");
  await page.getByRole("button", { name: "Weiteren Wechselrichter hinzufügen" }).click();
  // the form of the first inverter is on the page as well: the one for adding is the last
  await page.getByPlaceholder("z. B. 192.168.178.50").last().fill("192.168.178.60");
  await page.getByRole("button", { name: "Verbindung testen" }).last().click();
  await page.getByRole("button", { name: "Hinzufügen" }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0].inverters?.[1]).toEqual(expect.objectContaining({ name: "", host: "192.168.178.60", driver: "saj" }));
});

test("the expert settings of a further inverter can be changed in the app", async ({ page }) => {
  await open(page, "more/connection");
  const garage = page.getByTestId("inverter-wr2");
  await garage.getByText("Für Experten").click();
  await expect(garage.getByText("Zeitlimit pro Anfrage")).toBeVisible();
});

test("the control log names the inverter a battery change was for", async ({ page, api }) => {
  api.override("/api/control/log", { entries: [{ ts: Date.now() / 1000 - 60, action: "battery_settings",
    details: { from: { min_soc_on_grid: 20 }, to: { min_soc_on_grid: 25 }, inverter: "Garage" }, dry_run: false,
    result: "ok", note: null }] });
  await open(page, "more/control");
  await expect(page.getByText("Speicher-Einstellung von „Garage“ geändert")).toBeVisible();
});

test("an inverter away for long is named as left out of the sums", async ({ page, api }) => {
  api.live({ inverters: [{ id: "main", name: "Haus", connected: true, pv_power: 1000, battery_power: 0, battery_soc: 50 },
    { id: "wr2", name: "Garage", connected: false, left_out: true }] });
  await open(page, "");
  await expect(page.getByText("„Garage“ antwortet seit einigen Minuten nicht")).toBeVisible();
  await expect(page.getByText("zeigt die Übersicht keine Summe")).toHaveCount(0);
});

test("grid charging says it charges all batteries together", async ({ page, api }) => {
  const view = await demoRequest<Record<string, unknown>>("GET", "/api/charging");
  api.override("/api/charging", { ...view, batteries: [
    { id: "main", name: "Haus", active: true, last_error: null, reason: "im Ladefenster" },
    { id: "wr2", name: "", active: false, last_error: null, reason: "Ladeziel 80 % erreicht" }] });
  await open(page, "devices/charging");
  await expect(page.getByText("lädt alle Speicher gleichzeitig")).toBeVisible();
  await expect(page.getByText("Wechselrichter 2: Ladeziel 80 % erreicht")).toBeVisible();
});

test("an inverter of a parallel system can be marked as already in the first one", async ({ page }) => {
  const bodies: Settings["values"][] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().endsWith("/api/settings")) bodies.push(request.postDataJSON());
  });
  await open(page, "more/connection");
  const garage = page.getByTestId("inverter-wr2");
  await garage.getByRole("switch", { name: "Werte stecken schon im ersten Wechselrichter" }).click();
  await expect(garage.getByRole("switch", { name: "Misst einen eigenen Netzanschluss" })).toHaveCount(0);
  await garage.getByRole("button", { name: "Speichern" }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0].inverters).toEqual([expect.objectContaining({ id: "wr2", in_first: true })]);
});

test("an inverter already in the first one that does not answer leaves the sums as they are", async ({ page, api }) => {
  api.live({ inverters: [{ id: "main", name: "Haus", connected: true, pv_power: 1000, battery_power: 0, battery_soc: 50 },
    { id: "wr2", name: "Garage", connected: false, in_first: true }] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.sun-more")).toHaveCount(0);  // part of the first one's
  await expect(page.getByText("zeigt die Übersicht keine Summe")).toHaveCount(0);
});

test("the energy flow draws a sun and a battery for each inverter", async ({ page, api }) => {
  api.live({ pv_power: 6800, battery_power: -1300, battery_soc: 55, house_power: 5100, grid_power: -400, inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 3200, battery_power: -800, battery_soc: 62 },
    { id: "wr2", name: "Garage", connected: true, pv_power: 2100, battery_power: -500, battery_soc: 48 },
    { id: "wr3", name: "Stall", connected: true, pv_power: 1500, battery_power: null, battery_soc: null },
    { id: "wr4", name: "Keller", connected: true, pv_power: 900, battery_power: 0, battery_soc: 70, in_first: true }] });
  await open(page, "");
  const flow = page.locator(".flow");
  await expect(flow.locator(".flow-node.sun, .flow-node.sun-more")).toHaveCount(3);  // not the one in the first
  await expect(flow.locator(".flow-node.battery, .flow-node.battery-more")).toHaveCount(2);  // Stall has none
  await expect(flow.locator(".flow-node.battery-more")).toContainText("Garage");
  await expect(flow.locator(".flow-node.battery")).toContainText("Haus");
  await expect(flow).toHaveAttribute("aria-label", /Stall 1,50 kW/);
  expect(await layoutProblems(page)).toEqual([]);
});

test("more batteries than fit in the row are drawn as one with the sum", async ({ page, api }) => {
  const battery = (id: string, soc: number) => ({ id, name: id, connected: true, pv_power: 1000, battery_power: -300, battery_soc: soc });
  api.live({ battery_power: -900, battery_soc: 50, inverters: [battery("main", 40), battery("wr2", 50), battery("wr3", 60)] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.battery")).toContainText("0,90 kW");
  await expect(page.locator(".flow .flow-node.battery-more")).toHaveCount(0);
  await expect(page.locator(".flow .flow-node.sun-more")).toHaveCount(2);
});

test("the combined values stay visible below the energy flow", async ({ page, api }) => {
  api.live({ pv_power: 5300, battery_power: -1300, battery_soc: 55, house_power: 3600, grid_power: -400, inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 3200, battery_power: -800, battery_soc: 62 },
    { id: "wr2", name: "Garage", connected: true, pv_power: 2100, battery_power: -500, battery_soc: 48 }] });
  await open(page, "");
  await expect(page.locator(".flow-total")).toHaveText("Zusammen: Solar 5,30 kW · Speicher 1,30 kW lädt, 55 %");
});

test("the grid charging mark is on every battery that charges", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, devices: { ...status.devices, grid_charging: true } });
  api.live({ battery_power: -2000, grid_power: 0, inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 0, battery_power: null, battery_soc: null },
    { id: "wr2", name: "Garage", connected: true, pv_power: 0, battery_power: -500, battery_soc: 48 },
    { id: "wr3", name: "Stall", connected: true, pv_power: 0, battery_power: -1500, battery_soc: 30 }] });
  await open(page, "");
  // grid charging charges every battery (#619): both batteries drawn get "€"
  await expect(page.locator(".flow .flow-node.battery-more")).toHaveCount(2);
  await expect(page.locator(".flow .charge-source.grid")).toHaveCount(2);
});

test("an inverter already in the first one is named below the energy flow with its values", async ({ page, api }) => {
  api.live({ inverters: [{ id: "main", name: "Haus", connected: true, pv_power: 3000, battery_power: 0, battery_soc: 50 },
    { id: "wr2", name: "Garage", connected: true, pv_power: 2100, battery_power: 0, battery_soc: 48, in_first: true }] });
  await open(page, "");
  await expect(page.getByText("„Garage“ (im ersten Wechselrichter enthalten): Solar 2,10 kW · Speicher 48 %")).toBeVisible();
});

test("a PV inverter that is off at night gets no battery", async ({ page, api }) => {
  api.live({ inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 0, battery_power: 300, battery_soc: 50, has_pv: true, has_battery: true },
    { id: "wr2", name: "Dach", connected: false, has_pv: true, has_battery: false }] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.battery-more")).toHaveCount(0);
  await expect(page.locator(".flow .flow-node.sun-more")).toContainText("Dach");  // its sun stays, without values
});

test("the combined battery power says which way it goes", async ({ page, api }) => {
  api.live({ pv_power: 0, battery_power: 1300, battery_soc: 55, house_power: 1700, grid_power: 400, inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 0, battery_power: -1000, battery_soc: 62 },
    { id: "wr2", name: "Garage", connected: true, pv_power: 0, battery_power: 2300, battery_soc: 48 }] });
  await open(page, "");
  await expect(page.locator(".flow-total")).toContainText("Speicher 1,30 kW entlädt");
});

test("each battery says whether it charges or discharges", async ({ page, api }) => {
  api.live({ battery_power: 0, battery_soc: 55, inverters: [
    { id: "main", name: "Haus", connected: true, pv_power: 0, battery_power: 1500, battery_soc: 62 },
    { id: "wr2", name: "Garage", connected: true, pv_power: 0, battery_power: -1500, battery_soc: 48 }] });
  await open(page, "");
  await expect(page.locator(".flow .flow-node.battery")).toContainText("entlädt");
  await expect(page.locator(".flow .flow-node.battery-more")).toContainText(/(^|[^t])lädt/);
  await expect(page.locator(".flow")).toHaveAttribute("aria-label", /Garage 1,50 kW lädt, 48 %/);
});

for (const width of [360, 393]) {
  test(`two batteries do not overlap anything on a ${width} px phone`, async ({ page, api }) => {
    await page.setViewportSize({ width, height: 800 });
    api.live({ pv_power: 5300, battery_power: -1300, battery_soc: 55, house_power: 3600, grid_power: -400, inverters: [
      { id: "main", name: "Haus", connected: true, pv_power: 3200, battery_power: -800, battery_soc: 62 },
      { id: "wr2", name: "Garage", connected: true, pv_power: 2100, battery_power: -500, battery_soc: 48 }] });
    await open(page, "");
    const boxes = await page.locator(".flow .flow-node").evaluateAll((nodes) => nodes.map((n) => {
      const r = n.getBoundingClientRect();
      return { name: n.className, left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    }));
    const overlaps = boxes.flatMap((a, i) => boxes.slice(i + 1)
      .filter((b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom)
      .map((b) => `${a.name} / ${b.name}`));
    expect(overlaps).toEqual([]);
    // and nothing is cut off at the sides of the screen
    expect(boxes.filter((b) => b.left < 0 || b.right > width).map((b) => b.name)).toEqual([]);
  });
}
