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

  it("activates a ready candidate only through an explicit confirmation", async () => {
    const { ElMessageBox } = await import("element-plus");
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({
      action: "confirm",
    } as never);
    const activate = vi
      .spyOn(adminApi, "activateIndex")
      .mockResolvedValue({ ...index, status: "ACTIVE" });

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const button = wrapper.findAll("button").find((b) => b.text().includes("设为当前 RAG"));
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("将 Index v2 设为当前 RAG 索引"),
      "设为当前 RAG 索引",
      expect.objectContaining({ confirmButtonText: "确认切换" }),
    );
    expect(activate).toHaveBeenCalledWith("idx-2");
  });

  it("uses rollback only for a historical index", async () => {
    const { ElMessageBox } = await import("element-plus");
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
    const deprecated: KnowledgeIndex = { ...index, id: "idx-1", version: 1, status: "DEPRECATED" };
    adminStore.indexes = [deprecated];
    const rollback = vi
      .spyOn(adminApi, "rollbackRagIndex")
      .mockResolvedValue({ ...deprecated, status: "ACTIVE" });

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();
    const button = wrapper.findAll("button").find((b) => b.text().includes("回滚 RAG"));
    await button!.trigger("click");
    await flushPromises();

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
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("设为当前 RAG")),
    ).toBe(false);
  });

  it("shows task document progress instead of final zero counts while building", async () => {
    adminStore.indexes = [{ ...index, status: "BUILDING", document_count: 0, element_count: 0, chunk_count: 0 }];
    adminStore.latestIndexTasks = {
      [index.id]: {
        id: "task-building", status: "RUNNING", stage: "EMBEDDING", progress: 40, attempt: 1,
        index_id: index.id, error_code: null, error_message: null,
        metadata: { total_documents: 12, current_document_position: 5 },
        created_at: index.created_at, updated_at: index.created_at,
      },
    };

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("已处理 5/12 份文档");
    expect(wrapper.text()).not.toContain("0 份文档 · 0 个切片");
  });

  it("stops a running build cooperatively through the task cancel API", async () => {
    adminStore.indexes = [{ ...index, status: "BUILDING" }];
    adminStore.latestIndexTasks = {
      [index.id]: {
        id: "task-building", status: "RUNNING", stage: "PARSING", progress: 10, attempt: 1,
        index_id: index.id, error_code: null, error_message: null,
        metadata: { total_documents: 3, current_document_position: 1 },
        created_at: index.created_at, updated_at: index.created_at,
      },
    };
    const cancel = vi.spyOn(adminApi, "cancelTask").mockResolvedValue({
      ...adminStore.latestIndexTasks[index.id]!,
      task_type: "INDEX_BUILD",
      status: "CANCELLED",
      kb_id: "kb-1",
      document_id: null,
      started_at: index.created_at,
      finished_at: index.finished_at,
    } as const);

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const button = wrapper.findAll("button").find((b) => b.text() === "停止");
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(cancel).toHaveBeenCalledWith("task-building");
    expect(adminStore.notice).toContain("已请求停止构建");
  });

  it("retries a cancelled index through an explicit confirmation", async () => {
    const { ElMessageBox } = await import("element-plus");
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({
      action: "confirm",
    } as never);
    adminStore.indexes = [{ ...index, status: "CANCELLED" }];
    const retry = vi.spyOn(adminApi, "retryIndex").mockResolvedValue({
      requested: true,
      coalesced: false,
      index_id: "idx-3",
      task_id: "task-retry",
      rebuild_required: false,
    });
    const loadDetails = vi.spyOn(adminApi, "listIndexes").mockResolvedValue([]);
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([]);
    vi.spyOn(adminApi, "listDocuments").mockResolvedValue([]);

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const button = wrapper.findAll("button").find((b) => b.text() === "重建");
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining("自动跳过该文档"),
      "重建索引",
      expect.objectContaining({ confirmButtonText: "提交重建" }),
    );
    expect(retry).toHaveBeenCalledWith(index.id);
    expect(adminStore.notice).toContain("完成后需明确设为当前 RAG 索引");
    expect(loadDetails).toHaveBeenCalled();
  });

  it("offers deletion for cancelled and failed indexes like deprecated ones", async () => {
    adminStore.indexes = [
      { ...index, id: "idx-cancelled", status: "CANCELLED" },
      { ...index, id: "idx-failed", status: "FAILED" },
    ];

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const deleteButtons = wrapper.findAll("button").filter((b) => b.text() === "删除");
    expect(deleteButtons).toHaveLength(2);
  });

  it("surfaces the rebuild-required badge when documents diverge from the active index", async () => {
    adminStore.knowledgeBases = [
      {
        id: "kb-1",
        name: "产品库",
        description: null,
        status: "ENABLED",
        assistant_online: false,
        runtime_state: "UPDATING",
        active_index_id: "idx-2",
        building_index_id: null,
        rebuild_required: true,
        document_count: 2,
        active_chunk_count: 10,
        created_at: index.created_at,
        updated_at: index.created_at,
      },
    ];

    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("文档有变更待重建");
  });
});
