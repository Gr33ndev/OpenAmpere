import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/openampere/web", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8089", ws: true },
    },
  },
});
