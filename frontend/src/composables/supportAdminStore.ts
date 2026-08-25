import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { ExecutionPolicy, ProblemProfile, RuntimePolicy, TraceSummary } from "@/types/admin";

export const supportAdminStore = reactive({
  policy: null as RuntimePolicy | null,
  profiles: [] as ProblemProfile[],
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
      this.error = cause instanceof Error ? cause.message : "Support 管理操作失败";
    } finally {
      this.loading = false;
    }
  },

  async loadConfiguration(): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    if (!knowledgeId) {
      this.policy = null;
      this.profiles = [];
      return;
    }
    [this.policy, this.profiles] = await Promise.all([
      adminApi.getRuntimePolicy(knowledgeId),
      adminApi.listProblemProfiles(knowledgeId),
    ]);
  },

  async savePolicy(executionPolicy: ExecutionPolicy): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId;
    if (!knowledgeId) return;
    await this.run(async () => {
      this.policy = await adminApi.updateRuntimePolicy(knowledgeId, executionPolicy);
    }, "Support 运行策略已保存，新会话生效");
  },

  async loadErrors(): Promise<void> {
    const knowledgeId = adminStore.selectedKnowledgeId || undefined;
    this.errors = await adminApi.listTraces(knowledgeId, {
      engine: "SUPPORT",
      status: "FAILED",
      limit: 100,
    });
  },
});

export function useSupportAdminStore() {
  return supportAdminStore;
}

