import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/health": "http://127.0.0.1:18000",
      "/api": "http://127.0.0.1:18000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,vue}"],
      exclude: ["src/main.ts", "src/env.d.ts", "src/types/**"],
      // Vitest applies the global threshold to ALL files even when globs
      // match them (unlike Jest), so per-directory gates require removing
      // the global numbers entirely. Core logic keeps the strict 80% gate;
      // template-heavy UI layers use thresholds just below their current
      // level and should be raised as view-level tests grow.
      thresholds: {
        "src/api/**": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/composables/**": { statements: 80, branches: 80, functions: 75, lines: 80 },
        "src/utils/**": { statements: 80, branches: 80, functions: 80, lines: 80 },
        "src/components/**": { statements: 80, branches: 65, functions: 60, lines: 80 },
        "src/views/**": { statements: 80, branches: 75, functions: 35, lines: 80 },
        "src/App.vue": { statements: 80, branches: 80, functions: 30, lines: 80 },
      },
    },
  },
});
