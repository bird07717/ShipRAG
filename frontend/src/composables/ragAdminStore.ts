import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type {
  EngineRetrievalConfig,
  RagConfig,
  RagConfigUpdatePayload,
  TraceSummary,
} from "@/types/admin";

export const ragAdminStore = reactive({
  config: null as RagConfig | null,
  engineConfigs: [] as EngineRetrievalConfig[],
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
    this.config = await adminApi.getRagConfig();
  },

  async saveConfig(payload: RagConfigUpdatePayload): Promise<void> {
    await this.run(async () => {
      this.config = await adminApi.updateRagConfig(payload);
    }, "RAG 检索配置已保存，下一轮 RAG 调用生效");
  },

  async loadEngineConfigurations(): Promise<void> {
    this.engineConfigs = await adminApi.listEngineRetrievalConfigs();
  },

  async saveEngineConfig(
    policy: EngineRetrievalConfig["execution_policy"],
    payload: RagConfigUpdatePayload,
  ): Promise<void> {
    await this.run(async () => {
      const updated = await adminApi.updateEngineRetrievalConfig(policy, payload);
      const index = this.engineConfigs.findIndex((item) => item.execution_policy === policy);
      if (index >= 0) this.engineConfigs.splice(index, 1, updated);
      else this.engineConfigs.push(updated);
    }, "该路由策略的检索配置已保存，下一轮请求生效");
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
