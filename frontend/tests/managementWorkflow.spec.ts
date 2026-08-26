import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminStore } from "@/composables/adminStore";
import { adminApi } from "@/api/admin";
import IndexBuildView from "@/views/IndexBuildView.vue";
import type { KnowledgeIndex, KnowledgeRelease } from "@/types/admin";

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

describe("RAG index publication workflow", () => {
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

  it("keeps a READY candidate in Index Build until its Profile drafts are reviewed", async () => {
    const release: KnowledgeRelease = {
      id: "release-2",
      kb_id: "kb-1",
      index_id: "idx-2",
      index_version: 2,
      index_status: "READY",
      status: "DRAFT",
      validation_result: { valid: true, publishable: false, review_required_count: 1 },
      generation_error: null,
      draft_count: 1,
      approved_count: 0,
      drafts: [],
      created_at: "2026-08-25T00:00:00Z",
      updated_at: "2026-08-25T00:01:00Z",
      published_at: null,
    };
    adminStore.releases = [release];
    vi.spyOn(adminApi, "getKnowledgeRelease").mockResolvedValue(release);
    const activate = vi.spyOn(adminStore, "activateIndex").mockResolvedValue();
    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("审核发布");
    expect(wrapper.text()).toContain("审核并发布");

    const publish = wrapper.findAll("button").find((button) => button.text().includes("审核并发布"));
    await publish!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("候选发布");
    expect(wrapper.text()).toContain("仍有 1 条草稿待审核");
    expect(activate).not.toHaveBeenCalled();
  });
});
