// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const demo = process.env.VITE_DEMO === "1";

/** The demo is a page on the project website, not an installable app: no manifest, not indexed. */
const demoHtml: Plugin = {
  name: "openampere-demo-html",
  transformIndexHtml(html) {
    return html
      .replace(/\s*<link rel="manifest"[^>]*>/, "")
      .replace(/\s*<meta name="apple-mobile-web-app-[^>]*>/g, "")
      .replace("<title>OpenAmpere</title>", '<title>OpenAmpere – Demo</title>\n    <meta name="robots" content="noindex" />');
  },
};

export default defineConfig({
  plugins: demo ? [react(), demoHtml] : [react()],
  // relative paths: the demo lives in a sub folder of the GitHub Pages site, the Home Assistant app under the
  // ingress address (#300)
  base: "./",
  build: { outDir: demo ? "../_site/demo" : "../src/openampere/web", emptyOutDir: true },
  // the UI tests run on `vite preview`: same Content-Security-Policy as the server (CONTENT_POLICY in
  // src/openampere/api.py), so a test fails with a console error when the app needs more (#358)
  preview: {
    headers: { "Content-Security-Policy": "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; img-src 'self' data:" },
  },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8089", ws: true },
    },
  },
});
