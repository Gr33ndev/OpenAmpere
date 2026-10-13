// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #359: "Read + control" lets every user and automation of Home Assistant control, so the pairing card says so
test("the pairing card explains what the chosen permission allows", async ({ page, api }) => {
  const now = Date.now() / 1000;
  const tokens = await demoRequest<Record<string, unknown>>("GET", "/api/tokens");
  api.override("/api/tokens", { ...tokens, pairing: [
    { id: "p1", name: "Home Assistant", address: null, code: "123456", created: now, expires: now + 300 }] });
  await open(page, "more/apps");
  const card = page.locator(".pairing");
  await expect(card).toContainText("ändern kann sie nichts");
  await card.getByText("Lesen + Steuern").click();
  await expect(card).toContainText("alle Benutzer und alle Automationen");
  expect(await layoutProblems(page)).toEqual([]);
});
