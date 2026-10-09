import { fileURLToPath, URL } from "node:url";
import { execFileSync } from "node:child_process";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

function gitValue(args: string[]): string {
  try { return execFileSync("git", args, { cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); }
  catch { return ""; }
}
const commit = process.env.BUILD_COMMIT ?? gitValue(["rev-parse", "HEAD"]);
const buildInfo = {
  commit: /^[0-9a-f]{40,64}$/.test(commit) ? commit : null,
  built_at: process.env.BUILD_TIME === "unknown" ? null : process.env.BUILD_TIME ?? new Date().toISOString(),
  dirty: process.env.BUILD_DIRTY === "unknown" ? null
    : process.env.BUILD_DIRTY ? process.env.BUILD_DIRTY === "true" : !!gitValue(["status", "--porcelain", "--untracked-files=normal"]),
};

export default defineConfig({
  define: { "import.meta.env.APP_BUILD_INFO": JSON.stringify(buildInfo) },
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        // Long-lived vendor chunks stay cached across admin releases; views
        // are split separately through dynamic imports in AdminStudio/App.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("element-plus") || id.includes("@element-plus")) return "vendor-element-plus";
          if (id.includes("markdown-it") || id.includes("dompurify") || id.includes("linkify-it")
            || id.includes("mdurl") || id.includes("uc.micro") || id.includes("punycode")
            || id.includes("entities")) return "vendor-markdown";
          return "vendor";
        },
      },
    },
  },
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
