import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import IndexBuildView from "@/views/IndexBuildView.vue";
import SupportProfilesView from "@/views/SupportProfilesView.vue";
import type { KnowledgeIndex, KnowledgeRelease, ProblemProfile } from "@/types/admin";

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

const liveProfile: ProblemProfile = {
  id: "profile-1",
  knowledge_id: "kb-1",
  document_id: "doc-old",
  problem_code: "PDC_UPGRADE",
  canonical_question: "如何升级旧版 PDC？",
  aliases: ["旧版怎么升级"],
  keywords: ["升级"],
  product_name: "PDC",
  model_names: ["VDR600"],
  task_type: "UPGRADE",
  enabled: true,
  support_ready: true,
  readiness_reasons: [],
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
};

const release: KnowledgeRelease = {
  id: "release-2",
  kb_id: "kb-1",
  index_id: "idx-2",
  index_version: 2,
  index_status: "READY",
  status: "DRAFT",
  validation_result: {},
  generation_error: null,
  draft_count: 1,
  approved_count: 0,
  drafts: [
    {
      id: "draft-1",
      release_id: "release-2",
      kb_id: "kb-1",
      document_id: "doc-new",
      document_name: "PDC 新版升级",
      operation: "UPDATE",
      target_profile_id: "profile-1",
      target_problem_code: "PDC_UPGRADE",
      problem_code: "PDC_UPGRADE",
      canonical_question: "如何升级新版 PDC？",
      aliases: ["新版怎么升级"],
      keywords: ["升级"],
      product_name: "PDC",
      model_names: ["VDR600"],
      task_type: "UPGRADE",
      enabled: true,
      status: "DRAFT",
      generation_source: "LLM",
      confidence: 0.82,
      reasoning: "与线上问题高度相似，建议替换文档。",
      validation_result: {},
      created_at: "2026-08-25T00:00:00Z",
      updated_at: "2026-08-25T00:00:00Z",
    },
  ],
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:00Z",
  published_at: null,
};

describe("management release workflow", () => {
  beforeEach(() => {
    adminStore.selectedKnowledgeId = "kb-1";
    adminStore.indexes = [index];
    adminStore.latestIndexTasks = {};
    adminStore.knowledgeReleases = [release];
    adminStore.selectedRelease = release;
    adminStore.problemProfiles = [liveProfile];
    adminStore.documents = [
      {
        id: "doc-old",
        knowledge_id: "kb-1",
        filename: "old.docx",
        display_name: "PDC 旧版升级",
        file_size: 100,
        status: "STORED",
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      },
    ];
    adminStore.requestedView = "";
    adminStore.error = "";
    vi.spyOn(adminApi, "listProblemProfiles").mockResolvedValue([liveProfile]);
    vi.spyOn(adminApi, "listKnowledgeReleases").mockResolvedValue([release]);
    vi.spyOn(adminApi, "listProfileFeedback").mockResolvedValue([]);
    vi.spyOn(adminApi, "getKnowledgeRelease").mockResolvedValue(release);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("routes a release-managed READY index to review instead of direct activation", async () => {
    const wrapper = mount(IndexBuildView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("审核发布");
    expect(wrapper.text()).not.toContain("激活旧式快照");
    const review = wrapper.findAll("button").find((button) => button.text().includes("审核发布"));
    await review!.trigger("click");
    expect(adminStore.requestedView).toBe("support-profiles");
  });

  it("shows decision evidence and keeps the live profile section read-only", async () => {
    const wrapper = mount(SupportProfilesView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("82%");
    expect(wrapper.text()).toContain("发布历史");
    expect(wrapper.text()).toContain("此处只读展示当前线上配置");
    expect(wrapper.text()).not.toContain("新建问题文档");

    const edit = wrapper.findAll("button").find((button) => button.text() === "编辑");
    await edit!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("当前线上值");
    expect(wrapper.text()).toContain("如何升级旧版 PDC？");
    expect(wrapper.text()).toContain("与线上问题高度相似");
  });

  it("records a rejected draft as returned for modification", async () => {
    vi.spyOn(ElMessageBox, "confirm").mockImplementation(async () => "confirm" as never);
    const update = vi.spyOn(adminApi, "updateProfileDraft").mockResolvedValue({
      ...release.drafts![0]!,
      status: "REJECTED",
    });
    const wrapper = mount(SupportProfilesView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    const reject = wrapper.findAll("button").find((button) => button.text() === "退回");
    await reject!.trigger("click");
    await flushPromises();

    expect(update).toHaveBeenCalledWith("draft-1", { status: "REJECTED" });
  });
});
