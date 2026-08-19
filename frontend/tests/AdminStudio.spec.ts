import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import AdminStudio from "@/components/AdminStudio.vue";

describe("AdminStudio settings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("accepts and saves a service access credential", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) }),
    );

    const wrapper = mount(AdminStudio, {
      props: { view: "settings" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    const input = wrapper.get("input");
    await input.setValue("session-token");
    expect(input.element.value).toBe("session-token");

    await input.trigger("change");
    expect(sessionStorage.getItem("rag_service_token")).toBe("session-token");
    wrapper.unmount();
  });

  it("confirms and deletes a parsed document", async () => {
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([
      {
        id: "kb-1",
        name: "产品库",
        description: null,
        status: "ENABLED",
        runtime_state: "READY",
        active_index_id: "index-1",
        building_index_id: null,
        rebuild_required: false,
        document_count: 2,
        active_chunk_count: 10,
        created_at: "2026-08-17T00:00:00Z",
        updated_at: "2026-08-17T00:00:00Z",
      },
    ]);
    vi.spyOn(adminApi, "listDocuments").mockResolvedValue([
      {
        id: "doc-1",
        knowledge_id: "kb-1",
        filename: "manual.docx",
        display_name: "操作手册",
        file_size: 1024,
        status: "STORED",
        created_at: "2026-08-17T00:00:00Z",
        updated_at: "2026-08-17T00:00:00Z",
      },
    ]);
    vi.spyOn(adminApi, "listIndexes").mockResolvedValue([]);
    vi.spyOn(adminApi, "listTraces").mockResolvedValue([]);
    vi.spyOn(adminApi, "listModels").mockResolvedValue([]);
    vi.spyOn(adminApi, "listPrompts").mockResolvedValue([]);
    const deleteSpy = vi.spyOn(adminApi, "deleteDocument").mockResolvedValue({
      document_id: "doc-1",
      deleted: true,
      build_request: {
        requested: true,
        coalesced: false,
        index_id: "index-2",
        task_id: "task-2",
        rebuild_required: false,
      },
    });
    vi.spyOn(ElMessageBox, "confirm").mockImplementation(async () => "confirm" as never);

    const wrapper = mount(AdminStudio, {
      props: { view: "documents" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    const deleteButton = wrapper.findAll("button").find((button) => button.text() === "删除");
    expect(deleteButton).toBeDefined();
    await deleteButton?.trigger("click");
    await flushPromises();

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining("新索引发布前"),
      "删除文档",
      expect.objectContaining({ type: "warning" }),
    );
    expect(deleteSpy).toHaveBeenCalledWith("doc-1");
    expect(wrapper.text()).toContain("文档已删除，索引更新已提交");
    wrapper.unmount();
  });
});
