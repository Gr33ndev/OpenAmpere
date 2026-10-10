// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
// Fails when the app uses an API endpoint that the in-browser demo (src/demo/server.ts) does not know.
// The demo shares all UI code with the real app, only the data source differs, so every new endpoint
// needs a demo answer (or must be listed as write-only / not needed in the demo).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("../src/", import.meta.url).pathname;
const SERVER = join(SRC, "demo", "server.ts");

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "demo" ? [] : files(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const used = new Set();
for (const file of files(SRC)) {
  for (const match of readFileSync(file, "utf8").matchAll(/["`](\/api\/[A-Za-z0-9/_-]+)/g)) used.add(match[1]);
}

const server = readFileSync(SERVER, "utf8");
const routes = [...server.slice(server.indexOf("const ROUTES")).matchAll(/^\s+"(\/api\/[^"]+)":/gm)].map((m) => m[1]);
const listed = (name) => [...(server.match(new RegExp(`export const ${name} = \\[([^\\]]*)\\]`, "s"))?.[1] ?? "")
  .matchAll(/"([^"]+)"/g)].map((m) => m[1]);
const known = new Set([...routes, ...listed("DEMO_WRITE_ONLY"), ...listed("DEMO_NOT_NEEDED")]);

const missing = [...used].filter((path) => !known.has(path)).sort();
if (missing.length) {
  console.error("Diese API-Endpunkte haben keine Antwort in der Demo (web/src/demo/server.ts):");
  for (const path of missing) console.error(`  ${path}`);
  console.error("Bitte in ROUTES eine Demo-Antwort ergänzen oder als DEMO_WRITE_ONLY / DEMO_NOT_NEEDED eintragen.");
  process.exit(1);
}
console.log(`Demo deckt alle ${used.size} API-Endpunkte der App ab.`);
