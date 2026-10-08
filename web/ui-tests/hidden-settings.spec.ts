import type { Settings } from "../src/api";
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #164: without login /api/settings sends null for values that identify the owner or give access to data and lists
// them under "hidden"; the pages show a login hint instead of empty fields that could be saved over the real values
const PRIVATE = ["meter.username", "meter.meter_ids", "notify.ntfy_url"] as const;

async function loggedOut(api: { override(path: string, body: unknown): void }) {
  const settings = await demoRequest<Settings>("GET", "/api/settings");
  const values = { ...settings.values, ...Object.fromEntries(PRIVATE.map((key) => [key, null])) };
  api.override("/api/settings", { ...settings, values, hidden: [...PRIVATE] });
  api.override("/api/auth/status", { configured: true, authenticated: false });
}

for (const route of ["more/notify", "more/gridmeter"]) {
  test(`hidden settings ask for a login on ${route}`, async ({ page, api }) => {
    await loggedOut(api);
    await open(page, route);
    await expect(page.locator(".login-to-see").first()).toBeVisible();
    await expect(page.locator("input[value='null']")).toHaveCount(0);
    expect(await layoutProblems(page)).toEqual([]);
  });
}
