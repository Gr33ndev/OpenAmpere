// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

const CHANGELOG = {
  versions: [
    {
      version: "9.1.0", date: "2026-10-01", first: false, breaking: [],
      groups: {
        feat: [{ scope: "report", text: "Show something new" }], perf: [], other: [],
        fix: [{ scope: "security", text: "Restoring a crafted backup takes over unchecked data (GHSA-4jmw-6h58-vc78)" }],
      },
      summary: { de: "Kurz gesagt: etwas Neues.\n\nZweiter Absatz zu #176.", en: "In short: something new." },
    },
    {
      version: "9.0.0", date: "2026-09-01", first: true, breaking: [],
      groups: { feat: [{ scope: null, text: "Start" }], fix: [], perf: [], other: [] },
    },
  ],
};

// #176: the maintainer's summary of a version, above the list, in the app's language
test.describe("changelog summary", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/changelog.json", (route) => route.fulfill({ json: CHANGELOG }));
  });

  test("shows the German summary above the list", async ({ page }) => {
    await open(page, "more/changelog");
    const card = page.locator(".card.changelog").first();
    const summary = card.locator(".changelog-summary");
    await expect(summary.locator("p")).toHaveText(["Kurz gesagt: etwas Neues.", "Zweiter Absatz zu #176."]);
    await expect(summary.getByRole("link", { name: "#176" })).toBeVisible();
    const top = async (selector: string) => (await card.locator(selector).first().boundingBox())!.y;
    expect(await top(".changelog-summary")).toBeLessThan(await top("h3"));
    await expect(page.locator(".card.changelog").nth(1).locator(".changelog-summary")).toHaveCount(0);
    expect(await layoutProblems(page)).toEqual([]);
  });

  test("shows the English summary in English", async ({ page }) => {
    await page.goto("/?lang=en#/more/changelog");
    const summary = page.locator(".changelog-summary").first();
    await expect(summary).toHaveText("In short: something new.");
    await expect(summary).toHaveAttribute("lang", "en");
  });
});

test("links a security advisory in the list", async ({ page }) => {
  await page.route("**/changelog.json", (route) => route.fulfill({ json: CHANGELOG }));
  await open(page, "more/changelog");
  const link = page.getByRole("link", { name: "GHSA-4jmw-6h58-vc78" });
  await expect(link).toHaveAttribute("href", "https://github.com/Gr33ndev/OpenAmpere/security/advisories/GHSA-4jmw-6h58-vc78");
});
