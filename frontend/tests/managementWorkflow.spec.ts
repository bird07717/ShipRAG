import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import IndexBuildView from "@/views/IndexBuildView.vue";
import type { KnowledgeIndex } from "@/types/admin";

const index: KnowledgeIndex = {
  id: "idx-2",
  kb_id: "kb-1",
  version: 2,
  status: "READY",
  embedding_model_name: "bge-m3",
  embedding_dimension: 1024,
  bm25_engine: "pg",
  document_count: 2,
  element_count: 20,
  chunk_count: 10,
  build_reason: "DOCUMENT_CHANGED",
  activate_on_success: false,
  error_code: null,
  error_message: null,
  created_at: "2026-08-25T00:00:00Z",
  finished_at: "2026-08-25T00:01:00Z",
  activated_at: null,
};

describe("RAG index operations workspace", () => {
  beforeEach(() => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.indexes = [index];
    adminStore.latestIndexTasks = {};
    adminStore.latestProfileTasks = {};
    adminStore.releases = [];
    adminStore.releaseDetails = {};
    adminStore.error = "";
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps Index Build limited to technical operations", async () => {
    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("RAG 技术构建、任务诊断和回收");
    expect(wrapper.text()).not.toContain("发布 Support");
    expect(wrapper.text()).not.toContain("编辑 Profile 草稿");
    expect(wrapper.findAll("button").some((button) => button.text().includes("前往 Profile Review"))).toBe(false);
  });

  it("rolls the RAG index back only through an explicit confirmation", async () => {
    const { ElMessageBox } = await import("element-plus");
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({
      action: "confirm",
    } as never);
    const deprecated: KnowledgeIndex = { ...index, id: "idx-1", version: 1, status: "DEPRECATED" };
    adminStore.indexes = [deprecated];
    const rollback = vi
      .spyOn(adminApi, "rollbackRagIndex")
      .mockResolvedValue({ ...deprecated, status: "ACTIVE" });

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const button = wrapper.findAll("button").find((b) => b.text().includes("回滚 RAG"));
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("显式将 RAG 在线索引回滚到 Index v1"),
      "回滚 RAG Index",
      expect.objectContaining({ confirmButtonText: "确认回滚" }),
    );
    expect(rollback).toHaveBeenCalledWith("kb-1", "idx-1");
  });

  it("never offers a rollback for the building or currently active index", async () => {
    const building: KnowledgeIndex = { ...index, status: "BUILDING" };
    adminStore.indexes = [building];

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(
      wrapper.findAll("button").some((b) => b.text().includes("回滚 RAG")),
    ).toBe(false);
  });
});
