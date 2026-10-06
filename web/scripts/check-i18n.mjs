// Checks the translations in src/locales/ (#104): every entry must belong to a t("…") text that still exists in the
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
for (const name of readdirSync(LOCALES).filter((n) => n.endsWith(".json"))) {
  const dictionary = JSON.parse(readFileSync(join(LOCALES, name), "utf8"));
  for (const [german, translation] of Object.entries(dictionary)) {
    if (!used.has(german)) {
      console.error(`${name}: not used in the code (German text changed?): ${german}`);
      problems++;
    } else if (placeholders(german) !== placeholders(translation)) {
      console.error(`${name}: placeholders differ: ${german}`);
      problems++;
    }
  }
  const translated = [...used].filter((german) => dictionary[german]).length;
  console.log(`${name}: ${translated} of ${used.size} texts in t() translated.`);
}
if (problems) process.exit(1);
