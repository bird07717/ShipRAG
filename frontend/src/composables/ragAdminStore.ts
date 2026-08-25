import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type {
  ExecutionPolicy,
  RagConfig,
  RagConfigUpdatePayload,
  RuntimePolicy,
  TraceSummary,
} from "@/types/admin";

export const ragAdminStore = reactive({
  policy: null as RuntimePolicy | null,
  config: null as RagConfig | null,
  errors: [] as TraceSummary[],
  loading: false,
  error: "",
  notice: "",

  async run(action: () => Promise<void>, success = ""): Promise<void> {
    this.loading = true;
    this.error = "";
    this.notice = "";
    try {
      await action();
      this.notice = success;
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : "RAG 管理操作失败";
    } finally {
      this.loading = false;
    }
  },

  async loadConfiguration(): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    const policyPromise = knowledgeId
      ? adminApi.getRuntimePolicy(knowledgeId)
      : Promise.resolve(null);
    [this.policy, this.config] = await Promise.all([policyPromise, adminApi.getRagConfig()]);
  },

  async savePolicy(executionPolicy: ExecutionPolicy): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    if (!knowledgeId) return;
    await this.run(async () => {
      this.policy = await adminApi.updateRuntimePolicy(knowledgeId, executionPolicy);
    }, "RAG 运行策略已保存，新会话生效");
  },

  async saveConfig(payload: RagConfigUpdatePayload): Promise<void> {
    await this.run(async () => {
      this.config = await adminApi.updateRagConfig(payload);
    }, "RAG 检索配置已保存，下一轮 RAG 调用生效");
  },

  async loadErrors(): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId || undefined;
    this.errors = await adminApi.listTraces(knowledgeId, {
      engine: "RAG",
      status: "FAILED",
      limit: 100,
    });
  },
});

export function useRagAdminStore() {
  return ragAdminStore;
}

