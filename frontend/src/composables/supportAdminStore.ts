import { reactive } from "vue";

import { adminApi } from "@/api/admin";
import { adminStore } from "@/composables/adminStore";
import type { ProblemProfile, TraceSummary } from "@/types/admin";

export const supportAdminStore = reactive({
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
      this.profiles = [];
      return;
    }
    this.profiles = await adminApi.listProblemProfiles(knowledgeId);
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
