import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // @ts-ignore - vitest config in vite config
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"] },
});
