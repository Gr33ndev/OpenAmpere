// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { expect, open, test } from "./fixtures";

const WARNING = /Dieser Bericht enthält die vollständige Seriennummer/;

// #428: owners post reports in public issues; one with the full serial number must say it is not for that
test("a report with the full serial number warns not to post it publicly", async ({ page, api }) => {
  api.override("/api/diagnostics", { running: false, markdown: "Bericht", report: { created: Date.now() / 1000,
    full_serial: true, checks: [{ id: "blocks", title: "Registerkarte", status: "ok", summary: "lesbar" }] } });
  await open(page, "more/diagnostics");
  await expect(page.getByText(/Nur für eine private Nachricht an die Entwickler/)).toBeVisible();
  await expect(page.getByText(WARNING)).toBeVisible();
});

test("a masked report has no warning", async ({ page }) => {
  await open(page, "more/diagnostics");
  await expect(page.getByRole("button", { name: "Bericht kopieren" })).toBeVisible();
  await expect(page.getByText(WARNING)).toHaveCount(0);
});
