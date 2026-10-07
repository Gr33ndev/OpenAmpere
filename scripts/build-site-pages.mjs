#!/usr/bin/env node
/**
 * Renders the pages of the project website in every language (#104). Called by scripts/build-site.sh.
 * Usage (from the repository root): node scripts/build-site-pages.mjs [output dir, default _site]
 *
 * Templates: site/pages/<page>.html. Every visible text is a placeholder {{<page>.<section>.<name>}} with an English
 * key: <page> is the file site/locales/<lang>/<page>.json, the rest is the path inside it, e.g. {{index.hero.title}}
 * or {{common.footer.imprint}} (common.json holds the header and footer shared by all pages). Values may contain
 * inline HTML (links, <strong>), so a whole sentence stays one key. Inside an attribute (title, content, aria-label …)
 * a value is escaped as plain text.
 *
 * Reserved placeholders, filled by this script (also inside values):
 *   {{root}}            "" for German, "../" for the other languages: prefix for assets and the demo
 *   {{lang}}            code of the page's language, e.g. for the demo link {{root}}demo/?lang={{lang}}
 *   {{contentLang}}     language the texts are in, for <html lang>: the page's language, or German when it has no
 *                       site translation at all
 *   {{alternates}}      <link rel="alternate" hreflang> for every translated language plus x-default (German)
 *   {{languageSwitch}}  links to the same page in every translated language, the current one with aria-current
 *   {{changelog}}       every version of web/public/changelog.json (scripts/changelog.py, #155), headings from
 *                       changelog.list.* of the page's language; the entries are the commit titles
 * A template line that holds only a placeholder whose value is empty is left out.
 *
 * Languages: German (de) is the reference and goes to the root of the output (index.html, faq.html, impressum.html),
 * every other language to <output>/<lang>/ with the same file names. A language is every folder with a meta.json
 * ({"name": "English"}) in site/locales/ or in web/src/locales/: the demo of the web app links to ../<lang>/ for each
 * app language, so a language the app has but the website not yet gets pages with the German texts (named from the
 * app's meta.json). The language switch and hreflang list only languages with a site translation.
 *
 * Adding a language needs no code: copy site/locales/de/ to site/locales/<code>/, set the name in meta.json and
 * translate the values, keep the keys.
 *
 * Checks: a key missing in German or an unknown placeholder fails the build. A key missing in another language is
 * shown in German and reported as a warning with the count per language; German keys no template uses and keys
 * that exist only in a translation are reported too.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const REFERENCE = "de";
const SITE_URL = (process.env.SITE_URL ?? "https://gr33ndev.github.io/OpenAmpere/").replace(/\/?$/, "/");
const out = process.argv[2] ?? "_site";
const PLACEHOLDER = /\{\{\s*([^{}\s]+)\s*\}\}/g;
const RESERVED = new Set(["root", "lang", "contentLang", "alternates", "languageSwitch", "changelog"]);
const CHANGELOG_FILE = "web/public/changelog.json";
const CHANGELOG_KEYS = ["version", "first", "attention", "new", "fixed", "faster", "otherChanges"].map((k) => `changelog.list.${k}`);
const ISSUES = "https://github.com/Gr33ndev/OpenAmpere/issues/";

const errors = [];
const warnings = [];
const readJson = (path) => {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    errors.push(`${path}: ${error.message}`);
    return {};
  }
};

/** {code: meta} for every folder with a meta.json below dir. */
function metas(dir) {
  if (!existsSync(dir)) return {};
  return Object.fromEntries(readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && existsSync(join(dir, entry.name, "meta.json")))
    .map((entry) => [entry.name, readJson(join(dir, entry.name, "meta.json"))]));
}

const siteMetas = metas("site/locales");
const appMetas = metas("web/src/locales");
if (!siteMetas[REFERENCE]) errors.push(`site/locales/${REFERENCE}/meta.json is missing`);

/** texts[lang][file] = the parsed site/locales/<lang>/<file>.json */
const texts = {};
for (const lang of Object.keys(siteMetas)) {
  texts[lang] = {};
  for (const file of readdirSync(join("site/locales", lang))) {
    if (file.endsWith(".json") && file !== "meta.json") {
      texts[lang][file.slice(0, -5)] = readJson(join("site/locales", lang, file));
    }
  }
}

const byName = (a, b) => (a.code === REFERENCE ? -1 : b.code === REFERENCE ? 1 : a.name.localeCompare(b.name));
const languages = [...new Set([...Object.keys(siteMetas), ...Object.keys(appMetas)])]
  .map((code) => ({ code, name: siteMetas[code]?.name ?? appMetas[code]?.name ?? code, translated: code in siteMetas }))
  .sort(byName);
for (const { code } of languages) {
  if (!/^[a-z]{2,3}(-[A-Za-z0-9]+)*$/.test(code)) errors.push(`"${code}" is not a language code (folder name)`);
}
const translated = languages.filter((language) => language.translated);

/** The text for key ("index.hero.title") in lang, undefined if there is none. */
function lookup(lang, key) {
  const [file, ...path] = key.split(".");
  let node = texts[lang]?.[file];
  for (const part of path) node = node && typeof node === "object" ? node[part] : undefined;
  return typeof node === "string" ? node : undefined;
}

/** Every key of a texts file: ["hero.title", …] with the file name in front. */
function keysOf(node, prefix) {
  if (typeof node === "string") return [prefix];
  if (!node || typeof node !== "object") return [];
  return Object.entries(node).flatMap(([name, child]) => keysOf(child, `${prefix}.${name}`));
}

/** The versions as HTML: headings in the page's language, "#123" linked to the issue or pull request. */
function renderChangelog(text, contentLang) {
  const item = (i) => `<li>${i.scope ? `<strong>${escapeText(i.scope)}:</strong> ` : ""}`
    + `${escapeText(i.text).replace(/#(\d+)/g, `<a href="${ISSUES}$1">#$1</a>`)}</li>`;
  const list = (items) => `<ul>${items.map(item).join("")}</ul>`;
  const part = (title, items) => (items.length ? `<h3>${title}</h3>${list(items)}` : "");
  return changelog.map((v) => {
    const date = new Date(`${v.date}T12:00:00Z`).toLocaleDateString(contentLang, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
    return `<section class="release" id="v${escapeText(v.version)}">`
      + `<h2>${text("changelog.list.version").replace("{version}", escapeText(v.version))}</h2>`
      + `<p class="date"><time datetime="${escapeText(v.date)}">${date}</time></p>`
      + (v.first ? `<p>${text("changelog.list.first")}</p>` : "")
      + part(text("changelog.list.attention"), v.breaking) + part(text("changelog.list.new"), v.groups.feat)
      + part(text("changelog.list.fixed"), v.groups.fix) + part(text("changelog.list.faster"), v.groups.perf)
      + part(text("changelog.list.otherChanges"), v.groups.other)
      + "</section>";
  }).join("\n    ");
}

const escapeText = (value) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const pagePath = (lang, page) => `${lang === REFERENCE ? "" : `${lang}/`}${page === "index" ? "" : `${page}.html`}`;

const pages = readdirSync("site/pages").filter((file) => file.endsWith(".html")).map((file) => file.slice(0, -5)).sort();
const templates = Object.fromEntries(pages.map((page) => [page, readFileSync(join("site/pages", page + ".html"), "utf8")]));

// keys the templates use: each must exist in German
const used = new Set(["common.languageSwitch.label"]); // used by the language switch
for (const [page, template] of Object.entries(templates)) {
  for (const [, name] of template.matchAll(PLACEHOLDER)) {
    if (RESERVED.has(name)) continue;
    if (!name.includes(".")) errors.push(`site/pages/${page}.html: unknown placeholder {{${name}}}`);
    else if (lookup(REFERENCE, name) === undefined) errors.push(`site/pages/${page}.html: {{${name}}} is missing in site/locales/${REFERENCE}/`);
    used.add(name);
  }
}
const changelogUsed = Object.values(templates).some((template) => template.includes("{{changelog}}"));
const changelog = changelogUsed ? readJson(CHANGELOG_FILE).versions ?? [] : [];
if (changelogUsed) {
  for (const key of CHANGELOG_KEYS) {
    if (lookup(REFERENCE, key) === undefined) errors.push(`{{changelog}} needs ${key} in site/locales/${REFERENCE}/`);
    used.add(key);
  }
}
const germanKeys = Object.entries(texts[REFERENCE] ?? {}).flatMap(([file, node]) => keysOf(node, file));
const unused = germanKeys.filter((key) => !used.has(key));
if (unused.length) warnings.push(`${unused.length} German text(s) no page uses: ${unused.join(", ")}`);
for (const lang of Object.keys(texts)) {
  if (lang === REFERENCE) continue;
  const extra = Object.entries(texts[lang]).flatMap(([file, node]) => keysOf(node, file)).filter((key) => lookup(REFERENCE, key) === undefined);
  if (extra.length) warnings.push(`${lang}: ${extra.length} text(s) not in German (renamed or removed?): ${extra.join(", ")}`);
}

if (!errors.length) {
  for (const language of languages) {
    const lang = language.code;
    const root = lang === REFERENCE ? "" : "../";
    const vars = { root, lang, contentLang: language.translated ? lang : REFERENCE };
    const fillVars = (value, where) => value.replace(PLACEHOLDER, (match, name) => {
      if (name in vars) return vars[name];
      errors.push(`${where}: unknown placeholder ${match} in the text`);
      return match;
    });
    const missing = new Set();
    const text = (key) => {
      let value = lookup(lang, key);
      if (value === undefined) {
        missing.add(key);
        value = lookup(REFERENCE, key);
      }
      return fillVars(value, `${key} (${lang})`);
    };
    const switchLanguages = translated.some((other) => other.code === lang) ? translated : [...translated, language].sort(byName);

    const dir = lang === REFERENCE ? out : join(out, lang);
    mkdirSync(dir, { recursive: true });
    for (const page of pages) {
      const alternates = [...translated.map(({ code }) => [code, code]), ["x-default", REFERENCE]]
        .map(([hreflang, code]) => `<link rel="alternate" hreflang="${hreflang}" href="${SITE_URL}${pagePath(code, page)}" />`)
        .join("\n  ");
      const languageSwitch = `<span class="lang-switch" role="group" aria-label="${escapeText(text("common.languageSwitch.label"))}">`
        + switchLanguages.map(({ code, name }) => `<a href="${(code === lang ? pagePath(REFERENCE, page) : root + pagePath(code, page)) || "./"}"`
          + ` hreflang="${code}" lang="${code}" title="${escapeText(name)}" aria-label="${escapeText(name)}"`
          + `${code === lang ? ' aria-current="page"' : ""}>${code.toUpperCase()}</a>`).join("")
        + "</span>";
      const generated = { ...vars, alternates, languageSwitch, changelog: changelogUsed ? renderChangelog(text, vars.contentLang) : "" };

      const html = templates[page].split("\n").flatMap((line) => {
        const rendered = line.replace(PLACEHOLDER, (match, name, offset) => {
          if (name in generated) return generated[name];
          const value = text(name);
          // inside a tag: the value is an attribute, so plain text
          return line.lastIndexOf("<", offset) > line.lastIndexOf(">", offset) ? escapeText(value) : value;
        });
        return /^\s*\{\{[^{}]+\}\}\s*$/.test(line) && !rendered.trim() ? [] : [rendered];
      }).join("\n");
      writeFileSync(join(dir, `${page}.html`), html);
    }
    if (!language.translated) warnings.push(`${lang}: no site translation in site/locales/${lang}/, all ${missing.size} texts in German`);
    else if (missing.size) warnings.push(`${lang}: ${missing.size} text(s) missing, shown in German: ${[...missing].join(", ")}`);
    console.log(`site pages: ${lang} (${language.name}) → ${join(dir, "")}`);
  }
}

for (const warning of warnings) console.warn(`warning: ${warning}`);
if (errors.length) {
  for (const error of errors) console.error(`error: ${error}`);
  process.exit(1);
}
