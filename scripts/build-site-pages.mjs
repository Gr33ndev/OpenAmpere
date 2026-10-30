#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
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
 *                       changelog.list.* of the page's language; the entries are the commit titles, above them the
 *                       version's summary in the page's language or German (#176)
 * A template line that holds only a placeholder whose value is empty is left out.
 *
 * Device knowledge (#512): every Markdown file of docs/devices/ (#384) becomes a page in every language at the same
 * path, docs/devices/<path>.html (README.md as index.html), rendered by scripts/markdown.mjs into the template
 * site/doc.html. The pages stay English (lang="en" on the article); only the frame around them is translated, from
 * site/locales/<lang>/docs.json. Reserved placeholders of that template, besides the ones above:
 *   {{home}}       the way back to the page's language root, e.g. "../../../" ({{root}} is the site's root as above)
 *   {{doc}}        the rendered page
 *   {{docTitle}}   its first heading and " | " for the <title>, empty on the overview (README.md)
 *   {{docSource}}  the Markdown file on GitHub
 * A link to another page of docs/devices/ stays relative (.md → .html), a link to any other file of the repository
 * goes to the file on GitHub, a link with another scheme than https:// is left out (its text stays). A link to a file
 * or heading that does not exist is reported as a warning.
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
 * that exist only in a translation are reported too. Values are HTML, so every value of every language may only use
 * the tags of SAFE_TAGS (#355): <a href> to an https:// or relative address, <strong>, <em>, <code>, <br>, <p>, no
 * other attributes. Anything else (<script>, onclick=, a javascript: link, a "<" that is not such a tag) fails the
 * build; write a "<" in a text as &lt;. The check runs again on every value after the reserved placeholders are filled
 * in for each language ({{root}} is empty on German pages, so "{{root}}javascript:" would become a scheme), and no
 * page is written when it fails.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, posix } from "node:path";
import { renderMarkdown } from "./markdown.mjs";

const REFERENCE = "de";
const SITE_URL = (process.env.SITE_URL ?? "https://gr33ndev.github.io/OpenAmpere/").replace(/\/?$/, "/");
const out = process.argv[2] ?? "_site";
const PLACEHOLDER = /\{\{\s*([^{}\s]+)\s*\}\}/g;
const RESERVED = new Set(["root", "lang", "contentLang", "alternates", "languageSwitch", "changelog", "home", "doc", "docTitle", "docSource"]);
const CHANGELOG_FILE = "web/public/changelog.json";
const CHANGELOG_KEYS = ["version", "first", "attention", "new", "fixed", "faster", "otherChanges"].map((k) => `changelog.list.${k}`);
const ISSUES = "https://github.com/Gr33ndev/OpenAmpere/issues/";
const ADVISORIES = "https://github.com/Gr33ndev/OpenAmpere/security/advisories/";
const REPO_FILES = "https://github.com/Gr33ndev/OpenAmpere/blob/main/";
const DOCS = "docs/devices";
const DOC_TEMPLATE = "site/doc.html";

// tags a text value may contain, with the attributes each may have (#355)
const SAFE_TAGS = { a: ["href"], strong: [], em: [], code: [], br: [], p: [] };
const TAG = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[a-zA-Z][a-zA-Z0-9-]*="[^"<>]*")*)\s*\/?>/y;
// https:// or a relative address (no scheme, not //host); "&" only as &amp;, so no entity can hide a scheme
const SAFE_HREF = /^(?:https:\/\/|(?![a-zA-Z][a-zA-Z0-9+.-]*:|\/\/))(?:[^\s\0-\x1f\x7f"'<>\\&]|&amp;)*$/;

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

/** The versions as HTML: headings in the page's language, "#123" linked to the issue or pull request,
 *  "GHSA-xxxx-xxxx-xxxx" to the security advisory. */
function renderChangelog(text, contentLang) {
  const linked = (value) => escapeText(value).replace(/#(\d+)/g, `<a href="${ISSUES}$1">#$1</a>`)
    .replace(/GHSA(?:-[0-9a-z]{4}){3}/g, (id) => `<a href="${ADVISORIES}${id}">${id}</a>`);
  const item = (i) => `<li>${i.scope ? `<strong>${escapeText(i.scope)}:</strong> ` : ""}${linked(i.text)}</li>`;
  const list = (items) => `<ul>${items.map(item).join("")}</ul>`;
  const part = (title, items) => (items.length ? `<h3>${title}</h3>${list(items)}` : "");
  // the maintainer's summary (#176) in the page's language, else German (always there)
  const summary = (texts) => {
    const code = [contentLang, REFERENCE].find((c) => texts?.[c]);
    if (!code) return "";
    const paragraphs = texts[code].split(/\n\s*\n/).map((p) => `<p>${linked(p)}</p>`).join("");
    return `<div class="summary"${code === contentLang ? "" : ` lang="${code}"`}>${paragraphs}</div>`;
  };
  return changelog.map((v) => {
    const date = new Date(`${v.date}T12:00:00Z`).toLocaleDateString(contentLang, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
    return `<section class="release" id="v${escapeText(v.version)}">`
      + `<h2>${text("changelog.list.version").replace("{version}", escapeText(v.version))}</h2>`
      + `<p class="date"><time datetime="${escapeText(v.date)}">${date}</time></p>`
      + summary(v.summary)
      + (v.first ? `<p>${text("changelog.list.first")}</p>` : "")
      + part(text("changelog.list.attention"), v.breaking) + part(text("changelog.list.new"), v.groups.feat)
      + part(text("changelog.list.fixed"), v.groups.fix) + part(text("changelog.list.faster"), v.groups.perf)
      + part(text("changelog.list.otherChanges"), v.groups.other)
      + "</section>";
  }).join("\n    ");
}

const escapeText = (value) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const langPrefix = (lang) => (lang === REFERENCE ? "" : `${lang}/`);
const pagePath = (lang, page) => `${langPrefix(lang)}${page === "index" ? "" : `${page}.html`}`;

const pages = readdirSync("site/pages").filter((file) => file.endsWith(".html")).map((file) => file.slice(0, -5)).sort();
const templates = Object.fromEntries(pages.map((page) => [page, readFileSync(join("site/pages", page + ".html"), "utf8")]));
const docTemplate = existsSync(DOC_TEMPLATE) ? readFileSync(DOC_TEMPLATE, "utf8") : null;

// keys the templates use: each must exist in German
const used = new Set(["common.languageSwitch.label"]); // used by the language switch
const templateFiles = [...pages.map((page) => [`site/pages/${page}.html`, templates[page]]), ...(docTemplate ? [[DOC_TEMPLATE, docTemplate]] : [])];
for (const [file, template] of templateFiles) {
  for (const [, name] of template.matchAll(PLACEHOLDER)) {
    if (RESERVED.has(name)) continue;
    if (!name.includes(".")) errors.push(`${file}: unknown placeholder {{${name}}}`);
    else if (lookup(REFERENCE, name) === undefined) errors.push(`${file}: {{${name}}} is missing in site/locales/${REFERENCE}/`);
    used.add(name);
  }
}

/** Every Markdown file below dir, relative to it ("foxess/h3.md"). */
function markdownFiles(dir, prefix = "") {
  return readdirSync(join(dir, prefix), { withFileTypes: true }).flatMap((entry) => {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    return entry.isDirectory() ? markdownFiles(dir, path) : entry.name.endsWith(".md") ? [path] : [];
  }).sort();
}
const docOut = (file) => `${DOCS}/${file.replace(/(^|\/)README\.md$/, "$1index.md").replace(/\.md$/, ".html")}`;
const decode = (value) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};
const anchors = []; // [file, target file, heading id] of every link to a heading of a page, checked when all are rendered
/** Where the link href of the Markdown file `from` (relative to DOCS) points on the website, null for none. */
function docLink(from, href) {
  if (/^https:\/\//i.test(href)) return href;
  if (href.startsWith("#")) {
    anchors.push([from, from, decode(href.slice(1))]);
    return href;
  }
  const [path, hash] = href.split(/#(.*)/s);
  const target = posix.normalize(posix.join(posix.dirname(`${DOCS}/${from}`), decode(path)));
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:|^\//.test(href) || target.startsWith("../")) {
    warnings.push(`${DOCS}/${from}: the link to ${href} is left out (only https:// and files of the repository)`);
    return null;
  }
  if (!existsSync(target)) warnings.push(`${DOCS}/${from}: the link to ${href} leads nowhere (${target} does not exist)`);
  if (target.startsWith(`${DOCS}/`) && target.endsWith(".md")) {
    const file = target.slice(DOCS.length + 1);
    if (hash) anchors.push([from, file, decode(hash)]);
    return posix.relative(posix.dirname(docOut(from)), docOut(file)) + (hash ? `#${hash}` : "");
  }
  return REPO_FILES + target.split("/").map(encodeURIComponent).join("/") + (hash ? `#${hash}` : "");
}
// rendered once, the same in every language
const docs = docTemplate && existsSync(DOCS) ? markdownFiles(DOCS).map((file) => {
  const { html, title, ids } = renderMarkdown(readFileSync(join(DOCS, file), "utf8"), { link: (href) => docLink(file, href) });
  if (!title) errors.push(`${DOCS}/${file}: the page has no heading "# …" for its title`);
  return { file, out: docOut(file), html, title: title ?? file, ids };
}) : [];
for (const [from, file, id] of anchors) {
  const target = docs.find((doc) => doc.file === file);
  if (target && !target.ids.includes(id)) warnings.push(`${DOCS}/${from}: the link to ${file}#${id} leads to no heading`);
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
/** Why value is not safe to insert as HTML, undefined if it is (only SAFE_TAGS, see above). */
function unsafeHtml(value) {
  for (let start = value.indexOf("<"); start >= 0; start = value.indexOf("<", start + 1)) {
    TAG.lastIndex = start;
    const match = TAG.exec(value);
    if (!match) return `"${value.slice(start, start + 20)}…" is not a simple tag (write a "<" in a text as &lt;)`;
    const [tag, closing, name, attributes] = match;
    const allowed = Object.hasOwn(SAFE_TAGS, name.toLowerCase()) && SAFE_TAGS[name.toLowerCase()];
    if (!allowed) return `<${name}> is not allowed (only ${Object.keys(SAFE_TAGS).join(", ")})`;
    for (const [, attribute, address] of attributes.matchAll(/([a-zA-Z][a-zA-Z0-9-]*)="([^"]*)"/g)) {
      if (closing || !allowed.includes(attribute.toLowerCase())) return `${tag} has the attribute ${attribute}, which is not allowed`;
      if (!SAFE_HREF.test(address)) return `${tag} links to an address that is neither https:// nor relative`;
    }
  }
  return undefined;
}
for (const [lang, files] of Object.entries(texts)) {
  for (const key of Object.entries(files).flatMap(([file, node]) => keysOf(node, file))) {
    const problem = unsafeHtml(lookup(lang, key));
    if (problem) errors.push(`site/locales/${lang}/${key.split(".")[0]}.json: ${key}: ${problem}`);
  }
}

if (unused.length) warnings.push(`${unused.length} German text(s) no page uses: ${unused.join(", ")}`);
for (const lang of Object.keys(texts)) {
  if (lang === REFERENCE) continue;
  const extra = Object.entries(texts[lang]).flatMap(([file, node]) => keysOf(node, file)).filter((key) => lookup(REFERENCE, key) === undefined);
  if (extra.length) warnings.push(`${lang}: ${extra.length} text(s) not in German (renamed or removed?): ${extra.join(", ")}`);
}

// pages to write: [dir, file, html], written only when no check failed (also not the check of the filled values)
const output = [];
if (!errors.length) {
  for (const language of languages) {
    const lang = language.code;
    const root = lang === REFERENCE ? "" : "../";
    const vars = { root, lang, contentLang: language.translated ? lang : REFERENCE };
    // pageVars: vars with the root of the page (a page of the device knowledge lies deeper)
    const fillVars = (value, where, pageVars) => value.replace(PLACEHOLDER, (match, name) => {
      if (name in pageVars) return pageVars[name];
      errors.push(`${where}: unknown placeholder ${match} in the text`);
      return match;
    });
    const missing = new Set();
    const text = (key, pageVars = vars) => {
      let value = lookup(lang, key);
      if (value === undefined) {
        missing.add(key);
        value = lookup(REFERENCE, key);
      }
      const filled = fillVars(value, `${key} (${lang})`, pageVars);
      // the value as it goes into the page: the placeholders may complete a scheme ("{{root}}javascript:")
      const problem = unsafeHtml(filled);
      const error = `site/locales/${lang}/${key.split(".")[0]}.json: ${key}: ${problem} (after filling in the placeholders)`;
      if (problem && !errors.includes(error)) errors.push(error);
      return filled;
    };
    const switchLanguages = translated.some((other) => other.code === lang) ? translated : [...translated, language].sort(byName);

    const languageSwitch = (href) => `<span class="lang-switch" role="group" aria-label="${escapeText(text("common.languageSwitch.label"))}">`
      + switchLanguages.map(({ code, name }) => `<a href="${href(code)}"`
        + ` hreflang="${code}" lang="${code}" title="${escapeText(name)}" aria-label="${escapeText(name)}"`
        + `${code === lang ? ' aria-current="page"' : ""}>${code.toUpperCase()}</a>`).join("")
      + "</span>";
    /** <link rel="alternate"> to the same page in every translated language, path(code) its address in that one. */
    const alternates = (path) => [...translated.map(({ code }) => [code, code]), ["x-default", REFERENCE]]
      .map(([hreflang, code]) => `<link rel="alternate" hreflang="${hreflang}" href="${SITE_URL}${path(code)}" />`)
      .join("\n  ");
    const render = (template, generated) => template.split("\n").flatMap((line) => {
      const pageVars = { root: generated.root, lang, contentLang: vars.contentLang };
      const rendered = line.replace(PLACEHOLDER, (match, name, offset) => {
        if (name in generated) return generated[name];
        const value = text(name, pageVars);
        // inside a tag: the value is an attribute, so plain text
        return line.lastIndexOf("<", offset) > line.lastIndexOf(">", offset) ? escapeText(value) : value;
      });
      return /^\s*\{\{[^{}]+\}\}\s*$/.test(line) && !rendered.trim() ? [] : [rendered];
    }).join("\n");

    const dir = lang === REFERENCE ? out : join(out, lang);
    for (const page of pages) {
      const generated = { ...vars, alternates: alternates((code) => pagePath(code, page)), changelog: changelogUsed ? renderChangelog(text, vars.contentLang) : "",
        languageSwitch: languageSwitch((code) => (code === lang ? pagePath(REFERENCE, page) : root + pagePath(code, page)) || "./") };
      output.push([dir, `${page}.html`, render(templates[page], generated)]);
    }
    for (const doc of docs) {
      const home = "../".repeat(doc.out.split("/").length - 1);
      const path = (code) => `${langPrefix(code)}${doc.out.replace(/index\.html$/, "")}`;
      const name = posix.basename(doc.out);
      const generated = { ...vars, root: home + root, home, alternates: alternates(path), doc: doc.html,
        docTitle: doc.out === `${DOCS}/index.html` ? "" : `${escapeText(doc.title)} | `, docSource: `${REPO_FILES}${DOCS}/${doc.file}`,
        languageSwitch: languageSwitch((code) => (code === lang ? (name === "index.html" ? "./" : name) : home + root + path(code))) };
      output.push([join(dir, posix.dirname(doc.out)), name, render(docTemplate, generated)]);
    }
    if (!language.translated) warnings.push(`${lang}: no site translation in site/locales/${lang}/, all ${missing.size} texts in German`);
    else if (missing.size) warnings.push(`${lang}: ${missing.size} text(s) missing, shown in German: ${[...missing].join(", ")}`);
    console.log(`site pages: ${lang} (${language.name}) → ${join(dir, "")}`);
  }
}
if (!errors.length) {
  for (const [dir, file, html] of output) {
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, file), html);
  }
}

for (const warning of warnings) console.warn(`warning: ${warning}`);
if (errors.length) {
  for (const error of errors) console.error(`error: ${error}`);
  process.exit(1);
}
