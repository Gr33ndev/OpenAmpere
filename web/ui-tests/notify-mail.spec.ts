// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import type { Settings } from "../src/api";
import { demoRequest } from "../src/demo/server";
import { expect, layoutProblems, open, test } from "./fixtures";

// #594: notifications by e-mail through the owner's own mail server, on the page "Benachrichtigungen"
async function withMail(api: { override(path: string, body: unknown): void }, values: Partial<Settings["values"]>) {
  const settings = await demoRequest<Settings>("GET", "/api/settings");
  api.override("/api/settings", { ...settings, values: { ...settings.values, ...values },
    secrets: { ...settings.secrets, "notify.mail_password": { set: !!values["notify.mail_host"], hint: null } } });
}

test("a failed e-mail and the waiting messages are shown", async ({ page, api }) => {
  await withMail(api, { "notify.mail_host": "smtp.example.com", "notify.mail_user": "owner@example.com" });
  api.override("/api/notify/status", { mail: { error: "Der Mailserver antwortet nicht.", since: 1, queued: 2, paused: false } });
  await open(page, "more/notify");
  await expect(page.getByText("Die letzte E-Mail ließ sich nicht senden: Der Mailserver antwortet nicht.")).toBeVisible();
  await expect(page.getByText("2 Nachrichten warten.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "E-Mails ausschalten" })).toBeVisible();
  expect(await layoutProblems(page)).toEqual([]);
});

test("the port follows the encryption unless it was changed by hand", async ({ page }) => {
  await open(page, "more/notify");
  const port = page.getByRole("spinbutton", { name: "Port" });
  const encryption = page.getByRole("combobox", { name: "Verschlüsselung" });
  await expect(port).toHaveValue("587");
  await encryption.selectOption("tls");
  await expect(port).toHaveValue("465");
  await port.fill("2525");
  await encryption.selectOption("starttls");
  await expect(port).toHaveValue("2525");
  await expect(page.getByText("Noch nicht gespeichert")).toBeVisible();
});

test("the test e-mail saves the form first", async ({ page, api }) => {
  await withMail(api, {}); // also the answer to the PUT
  const requests: { method: string; path: string; body: unknown }[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" && path.startsWith("/api/")) {
      requests.push({ method: request.method(), path, body: request.postDataJSON() });
    }
  });
  await open(page, "more/notify");
  const send = page.getByRole("button", { name: "Test-E-Mail senden" });
  await expect(send).toBeDisabled(); // no server yet
  await page.getByRole("textbox", { name: "Postausgangsserver (SMTP)" }).fill("smtp.example.com");
  await page.getByRole("textbox", { name: "Benutzername" }).fill("owner@example.com");
  await page.getByLabel("Passwort").fill("app-password");
  await page.getByRole("textbox", { name: "Empfänger" }).fill("one@example.com\ntwo@example.com");
  await send.click();
  await expect(page.getByText("Kommt sie nicht an, schau auch im Spam-Ordner nach.")).toBeVisible();
  expect(requests.map((r) => `${r.method} ${r.path}`)).toEqual(["PUT /api/settings", "POST /api/notify/mail/test"]);
  expect(requests[0].body).toMatchObject({
    "notify.mail_host": "smtp.example.com", "notify.mail_port": 587, "notify.mail_security": "starttls",
    "notify.mail_user": "owner@example.com", "notify.mail_password": "app-password",
    "notify.mail_to": ["one@example.com", "two@example.com"],
  });
});
