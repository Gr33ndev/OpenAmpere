// Checks the translations in src/locales/<lang>/*.json (#104): every entry must belong to a t("…") text that still exists in the
// code (otherwise it is stale after a German text was changed), and must use the same {placeholders}.
// Prints how many texts are wrapped in t() and how many of them are translated.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src/", import.meta.url).pathname;
const LOCALES = join(SRC, "locales");

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "locales" ? [] : files(path);
    return /\.(ts|tsx)$/.test(name) && name !== "i18n.ts" ? [path] : [];  // i18n.ts only has examples in comments
  });
}

// t("…") calls with a plain string literal (no template strings), as the convention in src/i18n.ts says
const used = new Set();
for (const file of files(SRC)) {
  for (const match of readFileSync(file, "utf8").matchAll(/\bt\(\s*"((?:[^"\\]|\\.)*)"/g)) used.add(JSON.parse(`"${match[1]}"`));
}

const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
let problems = 0;
for (const lang of readdirSync(LOCALES).filter((n) => statSync(join(LOCALES, n)).isDirectory())) {
  // one file per area of the app; the same German text in two files must not get two translations
  const dictionary = {};
  for (const name of readdirSync(join(LOCALES, lang)).filter((n) => n.endsWith(".json")).sort()) {
    const file = `${lang}/${name}`;
    for (const [german, translation] of Object.entries(JSON.parse(readFileSync(join(LOCALES, lang, name), "utf8")))) {
      if (german in dictionary && dictionary[german] !== translation) {
        console.error(`${file}: translated differently in another file: ${german}`);
        problems++;
      }
      dictionary[german] = translation;
      if (!used.has(german)) {
        console.error(`${file}: not used in the code (German text changed?): ${german}`);
        problems++;
      } else if (placeholders(german) !== placeholders(translation)) {
        console.error(`${file}: placeholders differ: ${german}`);
        problems++;
      }
    }
  }
  const translated = [...used].filter((german) => dictionary[german]).length;
  console.log(`${lang}: ${translated} of ${used.size} texts in t() translated.`);
}
if (problems) process.exit(1);
