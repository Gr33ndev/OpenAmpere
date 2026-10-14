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
