/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Proxy the backend so the Citation Canvas iframe is SAME-ORIGIN with the
    // app. A cross-origin iframe (app :5173, backend :8000) has a null
    // contentDocument, which silently breaks passage highlighting + dark-mode
    // injection, and a sandboxed cross-origin PDF won't render. Routing
    // /papers + /chunks through :5173 makes the iframe same-origin.
    proxy: {
      "/papers": "http://localhost:8000",
      "/chunks": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    css: false,
  },
});
