// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, layoutProblems, open, test } from "./fixtures";

const UPDATE = {
  current: "0.11.0", available: true, updater: true, requested: false, check: true, auto: false,
  latest: { version: "0.12.0", url: "https://github.com/Gr33ndev/OpenAmpere/releases", notes: "", published: null },
  status: null, checked: null, error: null,
};

// #132: on a wide screen the banner content spread over the whole window instead of the page column
test("update banner stays in the page column", async ({ page, api }) => {
  api.override("/api/update", UPDATE);
  await open(page, "");
  const banner = page.locator(".update-banner");
  await expect(banner).toContainText("0.12.0");
  const column = (await page.locator("main .page").first().boundingBox())!;
  for (const part of [banner.locator("span").first(), banner.getByRole("button").first(), banner.locator(".banner-close")]) {
    const box = (await part.boundingBox())!;
    expect(box.x, "starts inside the page column").toBeGreaterThanOrEqual(column.x - 1);
    expect(box.x + box.width, "ends inside the page column").toBeLessThanOrEqual(column.x + column.width + 1);
  }
  expect(await layoutProblems(page)).toEqual([]);
});
