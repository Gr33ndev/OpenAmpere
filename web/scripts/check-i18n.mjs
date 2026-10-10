// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
// Checks the translations in src/locales/<lang>/<area>.json (#104). German (de) is the reference.
// - every key used in the code with t("…") or tx("…") exists in German, and every German key is used
// - every other language only has keys that exist in German, with the same {placeholders} and plural forms
// - every language has a meta.json with its name
// Prints how much of each language is translated; missing texts appear in German in the app.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src/", import.meta.url).pathname;
const LOCALES = join(SRC, "locales");
const REFERENCE = "de";

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "locales" ? [] : files(path);
    return /\.(ts|tsx)$/.test(name) && name !== "i18n.tsx" ? [path] : [];  // i18n.tsx only has examples in comments
  });
}

const isPlural = (value) => typeof value === "object" && value !== null && "other" in value;
function flatten(object, prefix, out) {
  for (const [key, value] of Object.entries(object)) {
    if (typeof value === "string" || isPlural(value)) out[prefix + key] = value;
    else flatten(value, `${prefix}${key}.`, out);
  }
  return out;
}

function language(lang) {
  const folder = join(LOCALES, lang);
  const messages = {};
  for (const name of readdirSync(folder).filter((n) => n.endsWith(".json") && n !== "meta.json")) {
    flatten(JSON.parse(readFileSync(join(folder, name), "utf8")), `${name.slice(0, -5)}.`, messages);
  }
  return messages;
}

const placeholders = (value) => [...new Set((isPlural(value) ? Object.values(value).join(" ") : value)
  .match(/\{\w+\}/g) ?? [])].sort().join(",");

let problems = 0;
const problem = (text) => { console.error(text); problems++; };

const used = new Set();
for (const file of files(SRC)) {
  for (const match of readFileSync(file, "utf8").matchAll(/\btx?\(\s*"([\w.]+)"/g)) used.add(match[1]);
}

const reference = language(REFERENCE);
for (const key of used) if (!(key in reference)) problem(`${REFERENCE}: key used in the code but missing: ${key}`);
for (const key of Object.keys(reference)) if (!used.has(key)) problem(`${REFERENCE}: key not used in the code: ${key}`);

for (const lang of readdirSync(LOCALES).filter((n) => statSync(join(LOCALES, n)).isDirectory()).sort()) {
  if (!existsSync(join(LOCALES, lang, "meta.json"))) problem(`${lang}: meta.json with the name of the language is missing`);
  if (lang === REFERENCE) continue;
  const messages = language(lang);
  for (const [key, value] of Object.entries(messages)) {
    if (!(key in reference)) problem(`${lang}: key does not exist in ${REFERENCE}: ${key}`);
    else if (isPlural(reference[key]) !== isPlural(value)) problem(`${lang}: plural forms differ from ${REFERENCE}: ${key}`);
    else if (placeholders(reference[key]) !== placeholders(value)) problem(`${lang}: placeholders differ: ${key}`);
  }
  const translated = Object.keys(reference).filter((key) => key in messages).length;
  console.log(`${lang}: ${translated} of ${Object.keys(reference).length} texts translated.`);
}
if (problems) process.exit(1);
