// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, PAGES, scrollProblems, test } from "./fixtures";

// every page opens without errors and keeps the layout rules, also while scrolling down and up again
for (const route of PAGES) {
  test(`page /${route}`, async ({ page }, info) => {
    // only in Playwright's WebKit, which draws date fields like Safari on a Mac. Checked in the iOS 27 Simulator:
    // Safari on the iPhone shows the field in its column and the page does not move sideways (#137)
    test.fixme(route === "more/tariff" && info.project.name === "iphone", "date field makes the page 11px wider in Playwright's WebKit only");
    await open(page, route);
    await expect(page.locator("main")).not.toBeEmpty();
    expect(await layoutProblems(page)).toEqual([]);
    expect(await scrollProblems(page)).toEqual([]);
  });
}
