// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Settings } from "../src/api";
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #437: own host names on the page "Zugriffsschutz", only after login, read-only when set by an environment variable
async function withHosts(api: { override(path: string, body: unknown): void }, hosts: string[] | null, locked = false) {
  const settings = await demoRequest<Settings>("GET", "/api/settings");
  api.override("/api/settings", { ...settings, values: { ...settings.values, "server.allowed_hosts": hosts },
    locked: locked ? ["server.allowed_hosts"] : [], hidden: hosts === null ? ["server.allowed_hosts"] : [] });
}

test("own addresses can be entered after login", async ({ page, api }) => {
  await withHosts(api, ["solar.example.de"]);
  await open(page, "more/security");
  const field = page.getByRole("textbox", { name: "Erlaubte Namen" });
  await expect(field).toHaveValue("solar.example.de");
  await expect(page.getByRole("button", { name: "Speichern" })).toBeDisabled();
  await field.fill("solar.example.de\nproxy.example.de");
  await expect(page.getByText("Noch nicht gespeichert")).toBeVisible();
  await expect(page.getByRole("button", { name: "Speichern" })).toBeEnabled();
  expect(await layoutProblems(page)).toEqual([]);
});

test("a wildcard from the config file is explained", async ({ page, api }) => {
  await withHosts(api, ["*"]);
  await open(page, "more/security");
  await expect(page.getByText("Damit ist der Schutz gegen DNS-Rebinding aus")).toBeVisible();
});

test("own addresses set by an environment variable are read-only", async ({ page, api }) => {
  await withHosts(api, ["solar.example.de"], true);
  await open(page, "more/security");
  await expect(page.getByRole("textbox", { name: "Erlaubte Namen" })).toBeDisabled();
});

test("own addresses are not shown without login", async ({ page, api }) => {
  await withHosts(api, null);
  api.override("/api/auth/status", { configured: true, authenticated: false });
  await open(page, "more/security");
  await expect(page.getByText("Eigene Adressen")).toHaveCount(0);
});
