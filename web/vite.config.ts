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
  // the demo lives in a sub folder of the GitHub Pages site, so all paths must be relative
  base: demo ? "./" : "/",
  build: { outDir: demo ? "../_site/demo" : "../src/openampere/web", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8089", ws: true },
    },
  },
});
