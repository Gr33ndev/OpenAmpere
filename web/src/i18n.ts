/**
 * Languages of the app (#104). German is the default and the source: every text is written in German in the code and
 * wrapped in t("…"). The German text is also the key into the dictionaries in src/locales/, so a text that is not
 * translated yet simply stays German. Placeholders are written as {name}: t("Version {version} ist da", { version }).
 *
 * The language is chosen per device under Mehr → Darstellung and applies after a reload.
 */
import en from "./locales/en.json";

export type Lang = "de" | "en";

const KEY = "openampere.lang";
const DICTIONARIES: Record<Exclude<Lang, "de">, Record<string, string>> = { en };

/** Choices for the language switch. Not fully translated languages are marked as a preview. */
export const LANGUAGES: [Lang, string][] = [["de", "Deutsch"], ["en", "English (Vorschau)"]];

export function storedLang(): Lang {
  try {
    const value = localStorage.getItem(KEY);
    if (value === "de" || value === "en") return value;
  } catch {
    /* private mode */
  }
  return "de";
}

let current: Lang = storedLang();
document.documentElement.lang = current;

export function lang(): Lang {
  return current;
}

/** Saves the language for this device and reloads, so every page renders in it. */
export function setLang(value: Lang): void {
  try { localStorage.setItem(KEY, value); } catch { /* private mode: only for this visit */ }
  current = value;
  window.location.reload();
}

/** The text in the chosen language, German if there is no translation yet. */
export function t(german: string, values?: Record<string, string | number>): string {
  const text = current === "de" ? german : DICTIONARIES[current][german] || german;
  return values ? text.replace(/\{(\w+)\}/g, (all, name: string) => (name in values ? String(values[name]) : all)) : text;
}
