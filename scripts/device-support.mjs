#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
/**
 * One source for which devices OpenAmpere supports and what EKD sold (#526): docs/devices/support.json.
 *
 * Usage (from the repository root):
 *   node scripts/device-support.mjs          writes the generated tables into the files of TARGETS
 *   node scripts/device-support.mjs --check  fails when the file is invalid or a generated table is out of date (CI)
 *
 * Generated between the markers of section() (HTML comments, hidden on GitHub and on the website):
 *   README.md, README.de.md          the table "Supported devices" from `devices`
 *   docs/devices/topics/names.md     the tables "Hardware sold by EKD over time" from `ekd`
 * scripts/build-site-pages.mjs renders the matrix of the EKD device page ({{devicesMatrix}}) and the device list on
 * the home page ({{devicesList}}) with siteMatrix() and siteList().
 *
 * The data:
 *   devices[]  id, name, does (Markdown, README only), short, confirmed, check (✅ in the README), state, next, page
 *              (its device page, the evidence behind "confirmed"), home: false to leave it off the home page
 *   ekd[]      id, group, names (the cells of its row in names.md: English Markdown, links relative to that page),
 *              and for the website: sold, device, period, state, note, issue or next; site: false leaves it off.
 *              support names a device: state, confirmation and next step come from it, a note of its own comes first.
 * A text is a string (the same in every language) or {de, en}. Website texts are plain text, escaped there.
 * state and next are keys of geraete.state.* and geraete.next.* in site/locales/<lang>/geraete.json.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const SUPPORT_FILE = "docs/devices/support.json";
const ISSUES = "https://github.com/Gr33ndev/OpenAmpere/issues/";
const LANGS = ["de", "en"];
const GROUPS = ["storage", "wallbox", "around"];
const NEXT = ["deviceReport", "tellUs"];
// state → class of the pill on the website (site/style.css)
const STATE_CLASS = { both: "ok", readTest: "ok", evcc: "part", relay: "part", planned: "plan", info: "info", notNeeded: "none", nothing: "none" };
// order on the website within a group: supported first, then planned, …; the same rank newest first
const RANK = { planned: 1, info: 2, notNeeded: 3, nothing: 4 };
const NAMES_COLUMNS = ["period", "sold", "model", "interface", "openampere", "status", "source"];
const NAMES_HEADER = ["Period (about)", "Sold as", "Manufacturer model", "Local interface", "OpenAmpere", "Status", "Source"];
const NAMES_GROUPS = { storage: "Storage systems and hybrid inverters:", wallbox: "Wallboxes:", around: "Around the storage system:" };
const README_HEADER = {
  de: ["Gerät", "Was OpenAmpere kann", "An echter Anlage bestätigt?"],
  en: ["Device", "What OpenAmpere does", "Confirmed on a real system?"],
};

/** The text in lang: a string is the same in every language; German when lang has none. */
export const pick = (value, lang) => (typeof value === "string" ? value : (value?.[lang] ?? value?.de));
const escapeHtml = (value) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Why the data is not valid, [] when it is. */
export function validate(data, exists = existsSync) {
  const errors = [];
  const text = (where, value, required = true) => {
    if (value === undefined && !required) return;
    const ok = typeof value === "string" ? value.trim() : value && LANGS.every((lang) => typeof value[lang] === "string" && value[lang].trim());
    if (!ok) errors.push(`${where}: needs a text, a string or {${LANGS.join(", ")}}`);
  };
  const ids = new Set();
  const unique = (list, kind) => {
    for (const entry of list) {
      if (typeof entry.id !== "string" || !entry.id) errors.push(`${kind}: an entry without id`);
      else if (ids.has(`${kind}:${entry.id}`)) errors.push(`${kind}.${entry.id}: the id is used twice`);
      ids.add(`${kind}:${entry.id}`);
    }
  };
  const devices = Array.isArray(data.devices) ? data.devices : [];
  const ekd = Array.isArray(data.ekd) ? data.ekd : [];
  if (!devices.length) errors.push("devices: missing or empty");
  if (!ekd.length) errors.push("ekd: missing or empty");
  unique(devices, "devices");
  unique(ekd, "ekd");
  const byId = new Map(devices.map((device) => [device.id, device]));
  for (const device of devices) {
    const where = `devices.${device.id}`;
    for (const key of ["name", "does", "confirmed"]) text(`${where}.${key}`, device[key]);
    if (device.home !== false) {
      text(`${where}.short`, device.short);
      if (!(device.state in STATE_CLASS)) errors.push(`${where}.state: one of ${Object.keys(STATE_CLASS).join(", ")}`);
    }
    if (device.next !== undefined && !NEXT.includes(device.next)) errors.push(`${where}.next: one of ${NEXT.join(", ")}`);
    if (device.page !== undefined && !(/^docs\/devices\/[\w./-]+\.md$/.test(device.page) && exists(device.page))) {
      errors.push(`${where}.page: ${device.page} is not a page of docs/devices/`);
    }
  }
  for (const entry of ekd) {
    const where = `ekd.${entry.id}`;
    if (!GROUPS.includes(entry.group)) errors.push(`${where}.group: one of ${GROUPS.join(", ")}`);
    for (const column of NAMES_COLUMNS) {
      if (typeof entry.names?.[column] !== "string" || !entry.names[column].trim()) errors.push(`${where}.names.${column}: missing`);
      else if (entry.names[column].includes("|")) errors.push(`${where}.names.${column}: a "|" would break the table`);
    }
    if (entry.site === false) continue;
    for (const key of ["sold", "device", "period"]) text(`${where}.${key}`, entry[key]);
    text(`${where}.note`, entry.note, entry.support === undefined);
    if (entry.support !== undefined) {
      const device = byId.get(entry.support);
      if (!device) errors.push(`${where}.support: there is no device "${entry.support}"`);
      else if (!(device.state in STATE_CLASS)) errors.push(`${where}.support: the device "${entry.support}" has no state`);
      if (entry.state !== undefined) errors.push(`${where}.state: comes from the device "${entry.support}"`);
    } else if (!(entry.state in STATE_CLASS)) errors.push(`${where}.state: one of ${Object.keys(STATE_CLASS).join(", ")}`);
    if (entry.issue !== undefined && !(Number.isInteger(entry.issue) && entry.issue > 0)) errors.push(`${where}.issue: an issue number`);
    if (entry.next !== undefined && !NEXT.includes(entry.next)) errors.push(`${where}.next: one of ${NEXT.join(", ")}`);
  }
  return errors;
}

/** The parsed file and why it is not valid; exists checks the device pages (the website build leaves that to CI). */
export function loadSupport(path = SUPPORT_FILE, exists = existsSync) {
  let data;
  try {
    data = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    return { data: { devices: [], ekd: [] }, errors: [`${path}: ${error.message}`] };
  }
  return { data, errors: validate(data, exists).map((error) => `${path}: ${error}`) };
}

const cell = (value) => value.replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ");

/** The table "Supported devices" of the README in lang (Markdown, links relative to the repository root). */
export function readmeTable(data, lang) {
  const rows = data.devices.map((device) => {
    const name = cell(pick(device.name, lang));
    return `| ${device.page ? `[${name}](${device.page})` : name} | ${cell(pick(device.does, lang))} | `
      + `${device.check ? "✅ " : ""}${cell(pick(device.confirmed, lang))} |`;
  });
  return [`| ${README_HEADER[lang].join(" | ")} |`, "|---|---|---|", ...rows].join("\n");
}

/** The tables "Hardware sold by EKD over time" of names.md, one per group, in the order of the file. */
export function namesTables(data) {
  return GROUPS.map((group) => [
    NAMES_GROUPS[group],
    "",
    `| ${NAMES_HEADER.join(" | ")} |`,
    `|${"---|".repeat(NAMES_HEADER.length)}`,
    ...data.ekd.filter((entry) => entry.group === group).map((entry) => `| ${NAMES_COLUMNS.map((c) => entry.names[c]).join(" | ")} |`),
  ].join("\n")).join("\n\n");
}

/** The entries of the website by group, each with its state, notes, next step and device page. */
function siteEntries(data) {
  const byId = new Map(data.devices.map((device) => [device.id, device]));
  const entries = data.ekd.map((entry, index) => ({ entry, index, device: byId.get(entry.support) }))
    .filter(({ entry }) => entry.site !== false);
  const rank = ({ entry, device }) => (device ? 0 : RANK[entry.state] ?? 9);
  return GROUPS.map((group) => [group, entries.filter(({ entry }) => entry.group === group)
    .sort((a, b) => rank(a) - rank(b) || b.index - a.index)]);
}

/**
 * The rows of the matrix on the EKD device page (the <tbody> content) in lang. t(key) is the website text of a key
 * (geraete.groups.*, geraete.state.*, geraete.next.*), already safe HTML.
 */
export function siteMatrix(data, lang, t) {
  const rows = [];
  for (const [group, entries] of siteEntries(data)) {
    if (!entries.length) continue;
    rows.push(`<tr class="group"><th colspan="5" scope="rowgroup">${t(`geraete.groups.${group}`)}</th></tr>`);
    for (const { entry, device } of entries) {
      const state = device ? device.state : entry.state;
      const notes = [entry.note && pick(entry.note, lang), device && `${pick(device.confirmed, lang)}.`].filter(Boolean);
      const deviceName = escapeHtml(pick(entry.device, lang));
      const page = device?.page && `${device.page.replace(/\.md$/, ".html").replace(/(^|\/)README\.html$/, "$1index.html")}`;
      const next = entry.issue ? `<a href="${ISSUES}${entry.issue}">${t("geraete.next.issue")} #${entry.issue}</a>`
        : (entry.next ?? device?.next) ? t(`geraete.next.${entry.next ?? device.next}`) : "–";
      rows.push("<tr>"
        + `<th scope="row">${escapeHtml(pick(entry.sold, lang))}</th>`
        + `<td>${page ? `<a href="${escapeHtml(page)}">${deviceName}</a>` : deviceName}</td>`
        + `<td>${escapeHtml(pick(entry.period, lang))}</td>`
        + `<td><span class="state ${STATE_CLASS[state]}">${t(`geraete.state.${state}`)}</span><small>${escapeHtml(notes.join(" "))}</small></td>`
        + `<td>${next}</td>`
        + "</tr>");
    }
  }
  return rows.join("\n");
}

/** The device list of the home page (<li> items) in lang. */
export function siteList(data, lang) {
  return data.devices.filter((device) => device.home !== false).map((device) => "<li>"
    + `<strong>${escapeHtml(pick(device.name, lang))}:</strong> ${escapeHtml(pick(device.short, lang))}. `
    + `${escapeHtml(pick(device.confirmed, lang))}.</li>`).join("\n");
}

/** Every key of the website texts siteMatrix() may use. */
export function siteKeys(data) {
  const states = new Set([...data.devices.map((d) => d.state), ...data.ekd.map((e) => e.state)].filter(Boolean));
  const next = new Set([...data.devices.map((d) => d.next), ...data.ekd.map((e) => e.next)].filter(Boolean));
  return ["geraete.next.issue", ...GROUPS.map((g) => `geraete.groups.${g}`), ...[...states].map((s) => `geraete.state.${s}`),
    ...[...next].map((n) => `geraete.next.${n}`)];
}

const begin = (name) => `<!-- generated:${name} from ${SUPPORT_FILE} by scripts/device-support.mjs; edit it there -->`;
const end = (name) => `<!-- /generated:${name} -->`;

/** content with the part between the markers of name replaced by body; throws when the markers are missing. */
export function section(content, name, body) {
  const start = content.indexOf(begin(name));
  const stop = content.indexOf(end(name));
  if (start < 0 || stop < start) throw new Error(`the markers ${begin(name)} … ${end(name)} are missing`);
  return `${content.slice(0, start)}${begin(name)}\n\n${body}\n\n${content.slice(stop)}`;
}

export const TARGETS = [
  ["README.md", "devices", (data) => readmeTable(data, "en")],
  ["README.de.md", "devices", (data) => readmeTable(data, "de")],
  ["docs/devices/topics/names.md", "ekd", (data) => namesTables(data)],
];

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const check = process.argv.includes("--check");
  const { data, errors } = loadSupport();
  const stale = [];
  if (!errors.length) {
    for (const [file, name, render] of TARGETS) {
      const content = readFileSync(file, "utf8");
      let updated;
      try {
        updated = section(content, name, render(data));
      } catch (error) {
        errors.push(`${file}: ${error.message}`);
        continue;
      }
      if (updated === content) continue;
      if (check) stale.push(file);
      else writeFileSync(file, updated);
      if (!check) console.log(`updated ${file}`);
    }
  }
  for (const error of errors) console.error(`error: ${error}`);
  for (const file of stale) console.error(`error: ${file} is out of date; run: node scripts/device-support.mjs`);
  if (errors.length || stale.length) process.exit(1);
}
