import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // @ts-expect-error - Vite v8's defineConfig overload does not include Vitest's
  // `test` key; remove when Vitest ships a merged type definition.
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
});
