// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { lang } from "./i18n";

export const REPO_URL = "https://github.com/Gr33ndev/OpenAmpere";
export const ISSUES_URL = `${REPO_URL}/issues`;
export const ADVISORIES_URL = `${REPO_URL}/security/advisories`;
export const LICENSES_DATA_URL = `${import.meta.env.BASE_URL}third-party-licenses.json`;
export const CHANGELOG_URL = `${import.meta.env.BASE_URL}changelog.json`;
export const SITE_URL = "https://gr33ndev.github.io/OpenAmpere/";
export const IMPRINT_URL = `${SITE_URL}impressum.html`;
/** README section on the old smartbox that overwrites settings (#25), linked from the "busy" hint (#311) and the
 *  dialog about an overwritten setting. */
export const SMARTBOX_GUIDE = () => (lang() === "de"
  ? `${REPO_URL}/blob/main/README.de.md#die-bisherige-smartbox-setzt-einstellungen-zur%C3%BCck`
  : `${REPO_URL}/blob/main/README.md#the-previous-smartbox-resets-settings`);
/** README section on custom host names and reverse proxies, linked when the live connection fails (#460). */
export const REVERSE_PROXY_GUIDE = () => (lang() === "de"
  ? `${REPO_URL}/blob/main/README.de.md#hinter-einem-reverse-proxy`
  : `${REPO_URL}/blob/main/README.md#behind-a-reverse-proxy`);
/** The guide for wallboxes without evcc: how to get the local key of a Tuya wallbox and set it up. */
export const WALLBOX_DIRECT_GUIDE = () => `${REPO_URL}/blob/main/docs/${lang() === "de" ? "wallbox-direct.de.md" : "wallbox-direct.md"}`;
/** The device knowledge on the website (#512): the pages of docs/devices/ in the app's language, page e.g. "foxess/h3.html". */
export const DEVICE_DOCS_URL = (page = "") => `${SITE_URL}${lang() === "de" ? "" : `${lang()}/`}docs/devices/${page}`;
const DRIVER_DOCS: Record<string, string> = { foxess: "foxess/h3.html", saj: "saj/h2-family.html" };
/** The page about the inverter of a driver, null for a driver without one. */
export const inverterDocsUrl = (driver: string | null | undefined) => (driver && DRIVER_DOCS[driver] ? DEVICE_DOCS_URL(DRIVER_DOCS[driver]) : null);
