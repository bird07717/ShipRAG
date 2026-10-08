import ElementPlus, { ElMessageBox } from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import AdminStudio from "@/components/AdminStudio.vue";
import { adminStore } from "@/composables/adminStore";

describe("AdminStudio settings", () => {
  beforeEach(() => {
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
    adminStore.error = "";
    adminStore.notice = "";
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    adminStore.serviceToken = "";
    adminStore.credentialRejected = false;
  });

  it("accepts and saves a service access credential", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) }),
    );

    const wrapper = mount(AdminStudio, {
      props: { view: "configuration", navigationQuery: "tab=credentials" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.find(".el-tabs__item.is-active").text()).toContain("服务凭证");
    const input = wrapper.get("input");
    await input.setValue("session-token");
    expect(input.element.value).toBe("session-token");

    const saveButton = wrapper.findAll("button").find((button) => button.text() === "保存并重新加载");
    await saveButton!.trigger("click");
    await flushPromises();
    expect(sessionStorage.getItem("rag_service_token")).toBe("session-token");
    expect(wrapper.text()).toContain("当前会话已配置");
    wrapper.unmount();
  });

  it("confirms and deletes a parsed document", async () => {
    adminStore.saveToken("session-token");
    vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([
      {
        id: "kb-1",
        name: "产品库",
        description: null,
        status: "ENABLED",
        assistant_online: true,
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
    // Success feedback is a transient toast rendered outside the view.
    expect(adminStore.notice).toBe("文档已删除，索引更新已提交");
    const toasts = Array.from(document.body.querySelectorAll(".el-message"), (item) => item.textContent ?? "");
    expect(toasts.some((text) => text.includes("文档已删除，索引更新已提交"))).toBe(true);
    expect(wrapper.find(".el-alert--success").exists()).toBe(false);
    wrapper.unmount();
  });

  it("grades notices: short toast for success, one sticky notification for caveats and warnings", async () => {
    // No credential: the view stays gated, but the notice presenter is still mounted.
    const wrapper = mount(AdminStudio, {
      props: { view: "dashboard" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    const notificationTexts = () => Array.from(document.body.querySelectorAll(".el-notification"), (item) => item.textContent ?? "");

    adminStore.announce("提示等级-警告：仍有 2 篇草稿待审核", "warning");
    await flushPromises();
    expect(notificationTexts().some((text) => text.includes("需要注意") && text.includes("提示等级-警告"))).toBe(true);
    expect(adminStore.noticeLevel).toBe("success"); // consumed and reset

    adminStore.announce("提示等级-说明：仅实际重新解析的文档使用新配置", "caveat");
    await flushPromises();
    expect(notificationTexts().some((text) => text.includes("请留意生效范围") && text.includes("提示等级-说明"))).toBe(true);

    adminStore.announce("提示等级-成功：已保存");
    await flushPromises();
    const toasts = Array.from(document.body.querySelectorAll(".el-message"), (item) => item.textContent ?? "");
    expect(toasts.some((text) => text.includes("提示等级-成功"))).toBe(true);
    expect(notificationTexts().some((text) => text.includes("提示等级-成功"))).toBe(false);
    expect(wrapper.find(".el-alert--success").exists()).toBe(false);
    wrapper.unmount();
  });

  it("lets the administrator close the error alert", async () => {
    adminStore.error = "保存失败";
    const wrapper = mount(AdminStudio, {
      props: { view: "dashboard" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    // ElIcon (the close button) is stubbed globally in tests; emit the alert's close.
    wrapper.findComponent({ name: "ElAlert" }).vm.$emit("close");
    expect(adminStore.error).toBe("");
    wrapper.unmount();
  });

  it("does not mount management views or poll without a service credential", async () => {
    const listKnowledgeBases = vi.spyOn(adminApi, "listKnowledgeBases").mockResolvedValue([]);

    const wrapper = mount(AdminStudio, {
      props: { view: "dashboard" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("需要配置服务凭证");
    expect(wrapper.text()).toContain("管理台已暂停自动加载和轮询");
    expect(listKnowledgeBases).not.toHaveBeenCalled();
    wrapper.unmount();
  });

});
