// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import type { Settings, Status } from "../src/api";
import { expect, layoutProblems, open, test } from "./fixtures";

// #619: more than one inverter in one installation (the demo has two: "Haus" and "Garage")
test("the overview shows each inverter with its own values", async ({ page }) => {
  await open(page, "");
  const title = page.locator(".section-title", { hasText: "Wechselrichter" });
  await expect(title).toBeVisible();
  const card = title.locator("xpath=following-sibling::div[1]");
  await expect(card).toContainText("Haus");
  await expect(card).toContainText("Garage");
  await expect(card).toContainText("Solar");
  expect(await layoutProblems(page)).toEqual([]);
});

test("an inverter without current values is shown as such", async ({ page, api }) => {
  api.live({ inverters: [{ id: "main", name: "Haus", connected: true, pv_power: 1000, battery_power: 0, battery_soc: 50 },
    { id: "wr2", name: "Garage", connected: false }] });
  await open(page, "");
  await expect(page.locator(".device-row-compact", { hasText: "Garage" })).toContainText("Keine aktuellen Werte");
  await expect(page.getByText("zeigt die Übersicht keine Summe")).toBeVisible();
});

test("with one inverter there are no inverter cards and no choice of inverter", async ({ page, api }) => {
  const status = await demoRequest<Status>("GET", "/api/status");
  api.override("/api/status", { ...status, inverters: [] });
  api.live({ inverters: [] });
  await open(page, "");
  await expect(page.locator(".section-title", { hasText: /^Wechselrichter$/ })).toHaveCount(0);
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

test("grid charging warns that it can move energy between the batteries", async ({ page }) => {
  await open(page, "devices/charging");
  await expect(page.getByText("von einem Speicher in den anderen")).toBeVisible();
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
  await expect(page.locator(".device-row-compact", { hasText: "Garage" })).toContainText("Keine aktuellen Werte");
  await expect(page.getByText("zeigt die Übersicht keine Summe")).toHaveCount(0);
});
