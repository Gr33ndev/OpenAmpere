// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { defineConfig, devices } from "@playwright/test";

/** UI tests (#137): the real app build, with the API answered by the demo simulation (ui-tests/fixtures.ts).
 *  Needs `npm run build` first; `npm run test:ui` runs them. */
const PORT = 4179;

export default defineConfig({
  testDir: "ui-tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, locale: "de-DE", timezoneId: "Europe/Berlin", trace: "retain-on-failure" },
  projects: [
    // a desktop browser, light
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1400, height: 900 }, colorScheme: "light" } },
    // an iPhone with Safari's engine, dark (most owners open the app on their phone)
    { name: "iphone", use: { ...devices["iPhone 15"], colorScheme: "dark" } },
  ],
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
  },
});
