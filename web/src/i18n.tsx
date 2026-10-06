/**
 * Languages of the app (#104).
 *
 * Texts are referenced by English keys: t("settings.tariffPage.title"). A key is "<area>.<component>.<name>": <area> is
 * the file src/locales/<lang>/<area>.json, the rest is the path inside it. Areas follow the navigation of the app
 * (shell, setup, overview, devices, report, settings) plus "common" for words used in many places.
 *
 * German (de) is the reference: every key exists there, and a key missing in another language falls back to German.
 *
 * Adding a language needs no code: create src/locales/<code>/ with a meta.json ({"name": "Français", "complete": false})
 * and the area files (copy the German ones and translate the values). `npm run check:i18n` shows what is missing.
 *
 * Values: t("shell.updates.versionAvailable", { version }) with "Version {version} ist da" in the file.
 * Plurals: the value is an object chosen by Intl.PluralRules with values.count, e.g. {"one": "1 Tag", "other": "{count} Tage"}.
 * Links or bold text inside a sentence: tx("…", { link: <a href=…>{t("…")}</a> }) with "Öffne {link} und …" in the
 * file, so every language can put the link where its word order needs it. Never split a sentence into several keys.
 * Lists: list(["Bezug", "Einspeisung"]) gives "Bezug und Einspeisung" in German, "Import and Feed-in" in English.
 */
import { Fragment, isValidElement, type ReactNode } from "react";
import type deCommon from "./locales/de/common.json";
import type deDevices from "./locales/de/devices.json";
import type deOverview from "./locales/de/overview.json";
import type deReport from "./locales/de/report.json";
import type deSettings from "./locales/de/settings.json";
import type deSetup from "./locales/de/setup.json";
import type deShell from "./locales/de/shell.json";

type Plural = { one?: string; other: string; zero?: string; two?: string; few?: string; many?: string };
type Messages = { [key: string]: string | Plural | Messages };
type Paths<T, P extends string = ""> = {
  [K in keyof T & string]: T[K] extends string | Plural ? `${P}${K}` : Paths<T[K], `${P}${K}.`>
}[keyof T & string];

/** Every key of the German reference files; a typo in a key is a type error. */
export type Key = Paths<{
  common: typeof deCommon; devices: typeof deDevices; overview: typeof deOverview; report: typeof deReport;
  settings: typeof deSettings; setup: typeof deSetup; shell: typeof deShell;
}>;
export type Lang = string;
type Values = Record<string, string | number>;

const STORAGE_KEY = "openampere.lang";
const REFERENCE = "de";

// every src/locales/<lang>/<area>.json and meta.json, found at build time
const files = import.meta.glob<{ default: Messages }>("./locales/*/*.json", { eager: true });
const metas = import.meta.glob<{ default: { name: string; complete?: boolean } }>("./locales/*/meta.json", { eager: true });
const parts = (path: string) => path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/)!.slice(1) as [string, string];

const DICTIONARIES: Record<string, Record<string, Messages>> = {};
for (const [path, file] of Object.entries(files)) {
  const [lang, area] = parts(path);
  if (area !== "meta") (DICTIONARIES[lang] ??= {})[area] = file.default;
}

/** Languages for the switch, German first; not complete ones are marked as a preview there. */
export const LANGUAGES: { code: Lang; name: string; complete: boolean }[] = Object.entries(metas)
  .map(([path, meta]) => ({ code: parts(path)[0], name: meta.default.name, complete: meta.default.complete !== false }))
  .sort((a, b) => (a.code === REFERENCE ? -1 : b.code === REFERENCE ? 1 : a.name.localeCompare(b.name)));

export function storedLang(): Lang {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value && value in DICTIONARIES) return value;
  } catch {
    /* private mode */
  }
  return REFERENCE;
}

let current: Lang = storedLang();
document.documentElement.lang = current;

export function lang(): Lang {
  return current;
}

/** Locale for numbers, dates and times, e.g. 1,5 kW and 06.10.2026 in German, 1.5 kW and 06/10/2026 in English. */
export const LOCALE = ({ de: "de-DE", en: "en-GB" } as Record<string, string>)[current] ?? current;
const plurals = new Intl.PluralRules(LOCALE);

/** Saves the language for this device and reloads, so every page renders in it. */
export function setLang(value: Lang): void {
  try { localStorage.setItem(STORAGE_KEY, value); } catch { /* private mode: only for this visit */ }
  current = value;
  window.location.reload();
}

function lookup(language: Lang, key: string): string | Plural | undefined {
  const [area, ...path] = key.split(".");
  let node: Messages | string | Plural | undefined = DICTIONARIES[language]?.[area];
  for (const part of path) node = node && typeof node === "object" ? (node as Messages)[part] : undefined;
  return typeof node === "string" || (node && typeof node === "object" && "other" in node) ? (node as string | Plural) : undefined;
}

function template(key: Key, values?: Values): string {
  const entry = lookup(current, key) ?? lookup(REFERENCE, key);
  if (entry === undefined) return key; // shows up in the app instead of breaking it; check:i18n catches it
  if (typeof entry === "string") return entry;
  const count = Number(values?.count ?? 0);
  return entry[plurals.select(count) as keyof Plural] ?? entry.other;
}

/** The text in the chosen language, German if it is not translated yet. */
export function t(key: Key, values?: Values): string {
  const text = template(key, values);
  return values ? text.replace(/\{(\w+)\}/g, (all, name: string) => (name in values ? String(values[name]) : all)) : text;
}

/** "A und B", "A, B und C" in the chosen language. */
export function list(items: string[]): string {
  return new Intl.ListFormat(LOCALE, { type: "conjunction" }).format(items);
}

/** Like t(), but values may be React elements, e.g. a link inside a sentence. */
export function tx(key: Key, values: Record<string, ReactNode>): ReactNode {
  return template(key, values as Values).split(/(\{\w+\})/).map((part, i) => {
    const name = part.match(/^\{(\w+)\}$/)?.[1];
    if (name === undefined || !(name in values)) return part;
    const value = values[name];
    return isValidElement(value) ? <Fragment key={i}>{value}</Fragment> : value;
  });
}
