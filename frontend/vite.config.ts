/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync } from "node:fs";
import path from "node:path";

const pkg = JSON.parse(
  readFileSync(path.resolve(__dirname, "package.json"), "utf-8"),
) as { version: string };

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: { port: 5173 },
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        present: path.resolve(__dirname, "present.html"),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // CSS is not processed, except a stylesheet a test reads as text (?raw).
    css: { include: [/\.css\?raw$/] },
    // Many jsdom workers in parallel (often beside the dev servers) push a few
    // multi-step tests past the 5 s default; a real hang still fails.
    testTimeout: 15_000,
    // Forked workers, two by default: one jsdom + BlockNote worker per core
    // ran the machine out of memory (workers exited mid-run).
    pool: "forks",
    maxWorkers: Number(process.env.VITEST_MAX_WORKERS) || 2,
  },
});
