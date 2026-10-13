// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, open, test } from "./fixtures";

// #329: the control log for an issue, as the server masked it, without dates and clock times
test("the control log is copied for a report without times", async ({ page, context, browserName }) => {
  test.skip(browserName !== "chromium", "clipboard permissions");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page, "more/control");
  await page.getByRole("button", { name: "Für einen Bericht kopieren" }).click();
  await expect(page.locator(".toast")).toContainText("Protokoll für den Bericht kopiert");
  const text = await page.evaluate(() => navigator.clipboard.readText());
  expect(text).toContain("## OpenAmpere-Steuerprotokoll");
  expect(text).toContain("vor 5 Stunden");
  expect(text).not.toMatch(/\d{1,2}:\d{2}/);  // no clock times
});
