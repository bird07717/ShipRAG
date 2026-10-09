import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { createApp } from "vue";
import { describe, expect, it } from "vitest";

import { installElementPlus } from "@/elementPlus";

// Vitest runs from the frontend package root (jsdom gives import.meta.url an http scheme).
const SRC = join(process.cwd(), "src");

function vueFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return vueFiles(path);
    return path.endsWith(".vue") ? [path] : [];
  });
}

function pascal(tag: string): string {
  return tag.split("-").map((part) => part[0]!.toUpperCase() + part.slice(1)).join("");
}

/*
 * Tests mount components with the full ElementPlus plugin, so a component
 * missing from the production registry would only show up in a real browser.
 * This scans every template for <el-*> tags and v-loading instead.
 */
describe("Element Plus on-demand registry", () => {
  const sources = vueFiles(SRC).map((file) => readFileSync(file, "utf8"));
  const app = createApp({});
  installElementPlus(app);
  const registered = app._context.components;

  it("registers every <el-*> component used in templates", () => {
    const tags = new Set(sources.flatMap((source) => [...source.matchAll(/<(el-[a-z-]+)/g)].map((match) => match[1]!)));
    expect(tags.size).toBeGreaterThan(10);
    const missing = [...tags].filter((tag) => !registered[pascal(tag)]);
    expect(missing).toEqual([]);
  });

  it("registers the v-loading directive", () => {
    expect(sources.some((source) => source.includes("v-loading"))).toBe(true);
    expect(app._context.directives.loading).toBeTruthy();
  });
});
