import { expect, open, test } from "./fixtures";

// #223: smaller bugs in the web app

test("the running week can be reached with next from the week before", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-12T10:00:00") }); // a Monday
  await open(page, "report");
  await page.getByRole("button", { name: "Zeitraum zurück" }).click(); // Sunday, 11.10.
  await page.getByRole("button", { name: "Woche", exact: true }).click();
  await expect(page.locator(".date-nav span").first()).toContainText("05.10.");
  const next = page.getByRole("button", { name: "Zeitraum weiter" });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.locator(".date-nav span").first()).toContainText("12.10.");
  await expect(next).toBeDisabled(); // no week in the future
});

test("removing a device asks first", async ({ page }) => {
  await open(page, "more/device-setup");
  await page.locator(".device-row-compact").first().click();
  await page.getByRole("button", { name: "Gerät entfernen" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("entfernen?");
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Gerät entfernen" })).toBeVisible(); // still there
});

test("a device card stays on screen while the devices load again after an action", async ({ page }) => {
  await open(page, "devices");
  const card = page.locator(".device-card", { hasText: "Heizstab" });
  await expect(card).toBeVisible();
  await page.evaluate(() => {
    (window as unknown as { vanished: boolean }).vanished = false;
    new MutationObserver(() => {
      if (![...document.querySelectorAll(".device-card")].some((c) => c.textContent?.includes("Heizstab"))) {
        (window as unknown as { vanished: boolean }).vanished = true;
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
  await page.route((url) => url.pathname === "/api/devices", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1000)); // a slow server
    await route.fallback();
  });
  await card.locator(".segmented button", { hasText: "Aus" }).click(); // reloads /api/devices
  await page.waitForTimeout(1800);
  expect(await page.evaluate(() => (window as unknown as { vanished: boolean }).vanished)).toBe(false);
});
