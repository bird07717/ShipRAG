<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { EditPen, RefreshRight } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import type { DocumentProfileIntent, DocumentProfileSummary, ProfilePublishCheck } from "@/types/admin";
import EmptyState from "@/components/ui/EmptyState.vue";

const store = useAdminStore();
const editorOpen = ref(false);
const editing = ref<DocumentProfileSummary | null>(null);
const lastCheck = ref<ProfilePublishCheck | null>(null);
const form = ref<DocumentProfileIntent>(emptyIntent());
let profileRefreshTimer: ReturnType<typeof setInterval> | undefined;

onMounted(() => {
  // RAG builds enqueue document Profile generation from a worker, so this
  // page may not have a browser-originated task to poll. Keep the review
  // snapshot current while it is open.
  void store.loadKnowledgeProfiles();
  profileRefreshTimer = setInterval(() => void store.loadKnowledgeProfiles(), 3_000);
});

onUnmounted(() => clearInterval(profileRefreshTimer));

function emptyIntent(): DocumentProfileIntent {
  return { canonical_question: "", aliases: [], routing_summary: "", enabled: true };
}
function firstIntent(row: DocumentProfileSummary): DocumentProfileIntent {
  const source = row.pending_profile?.intents[0] ?? row.active_profile?.intents[0];
  return source ? { ...emptyIntent(), ...source } : emptyIntent();
}
function lines(value: string): string[] { return value.split(/\n|,/).map((item) => item.trim()).filter(Boolean); }
const profiles = computed(() => store.knowledgeProfileSummary?.profiles ?? []);
const summary = computed(() => store.knowledgeProfileSummary);
const task = computed(() => store.latestDocumentProfileTasks[store.selectedKnowledgeId]);
const taskActive = computed(() => Boolean(task.value && ["QUEUED", "RUNNING"].includes(task.value.status)));
async function cancel(): Promise<void> {
  const current = task.value;
  if (!current) return;
  try {
    await ElMessageBox.confirm(
      "将停止本次生成任务：正在处理的文档会完成当前步骤，其余文档不再处理；已生成的待上线属性会保留。",
      "停止生成任务",
      { type: "warning", confirmButtonText: "确认停止", cancelButtonText: "继续运行" },
    );
  } catch {
    return;
  }
  await store.cancelDocumentProfileTask(current.id);
}
const publishReason = computed(() => {
  if (!store.selectedKnowledgeId) return "请选择知识库";
  if (!summary.value?.pending_count) return "没有可上线的待上线属性";
  return lastCheck.value && !lastCheck.value.publishable ? "检查未通过，请先处理冲突" : "";
});
const labels: Record<string, string> = { NOT_GENERATED: "未生成", GENERATING: "生成中", PENDING_ONLINE: "待上线", ONLINE: "已上线", NEEDS_ATTENTION: "需处理", DISABLED: "已停用", FAILED: "失败" };
function statusLabel(status: string): string { return labels[status] ?? status; }
function statusType(status: string): "success" | "warning" | "danger" | "info" {
  if (status === "ONLINE") return "success";
  if (status === "PENDING_ONLINE" || status === "GENERATING") return "warning";
  return status === "FAILED" ? "danger" : "info";
}
function openEditor(row: DocumentProfileSummary): void { editing.value = row; form.value = firstIntent(row); editorOpen.value = true; }
async function save(): Promise<void> {
  if (!editing.value) return;
  await store.updateDocumentProfile(editing.value.document_id, {
    canonical_question: form.value.canonical_question.trim(),
    aliases: form.value.aliases,
    routing_summary: form.value.routing_summary.trim(),
    enabled: form.value.enabled,
  });
  if (!store.error) { editorOpen.value = false; lastCheck.value = null; }
}
async function regenerate(row?: DocumentProfileSummary): Promise<void> { if (row) await store.generateDocumentProfile(row.document_id); else await store.generateKnowledgeProfiles(); }
async function check(): Promise<void> { lastCheck.value = await store.checkKnowledgeProfiles(); if (lastCheck.value?.publishable) store.announce("待上线变更检查通过，可以上线"); }
async function publish(): Promise<void> {
  if (publishReason.value) return;
  lastCheck.value = lastCheck.value ?? await store.checkKnowledgeProfiles();
  if (!lastCheck.value?.publishable) return;
  try { await ElMessageBox.confirm(`将原子上线当前知识库中 ${lastCheck.value.pending_count} 项已通过硬校验的 Profile 属性。低置信或失败项会保留为待处理，继续由 RAG 兜底。`, "上线 Profile 变更", { type: "warning", confirmButtonText: "确认上线", cancelButtonText: "取消" }); } catch { return; }
  await store.publishKnowledgeProfiles(); lastCheck.value = null;
}
async function discard(): Promise<void> {
  try { await ElMessageBox.confirm("将取消当前知识库的全部待上线 Profile 属性，不会改变已上线属性和 RAG。", "取消待上线变更", { type: "warning", confirmButtonText: "确认取消", cancelButtonText: "保留" }); } catch { return; }
  await store.discardPendingKnowledgeProfiles(); lastCheck.value = null;
}
watch(() => store.selectedKnowledgeId, () => { lastCheck.value = null; });
</script>

<template>
  <div class="support-review">
    <el-alert
      title="Support Profile 已冻结（ADR-0021）：实际运行均为 RAG_ONLY，Profile 不再参与在线问答。"
      description="本页仅用于维护历史兼容数据；重新发布 Profile 不会改变任何在线回答。"
      type="warning"
      :closable="false"
      show-icon
      class="phase-alert"
    />
    <el-alert
      title="Profile 仅保留标准问题、别名（Alias）和路由描述，用于高置信快速命中；低匹配或歧义时自动使用 RAG。"
      type="success"
      :closable="false"
      show-icon
      class="phase-alert"
    />
    <section class="panel">
      <div class="panel-body review-toolbar">
        <el-select
          v-model="store.selectedKnowledgeId"
          placeholder="选择知识库"
          style="width: 260px"
          @change="store.selectKnowledge()"
        >
          <el-option
            v-for="kb in store.knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id"
          />
        </el-select>
        <span class="muted">生成、编辑、检查和上线均在此页面完成。</span><div class="spacer" />
        <el-button :loading="store.isActionLoading(`document-profile-generate:${store.selectedKnowledgeId}`)" :disabled="!store.selectedKnowledgeId || taskActive" @click="regenerate()">重新生成全部待上线属性</el-button>
        <el-button :loading="store.isActionLoading(`document-profile-check:${store.selectedKnowledgeId}`)" :disabled="!store.selectedKnowledgeId" @click="check">检查待上线变更</el-button>
        <el-button
          type="primary"
          :loading="store.isActionLoading(`document-profile-publish:${store.selectedKnowledgeId}`)"
          :disabled="Boolean(publishReason)"
          @click="publish"
        >
          上线 Profile 变更
        </el-button>
      </div><small v-if="publishReason" class="action-hint">{{ publishReason }}</small>
    </section>
    <div class="review-metrics"><div class="metric-card compact"><span>线上属性</span><strong>{{ summary?.online_count ?? 0 }}</strong></div><div class="metric-card compact"><span>待上线</span><strong>{{ summary?.pending_count ?? 0 }}</strong></div><div class="metric-card compact"><span>需处理</span><strong>{{ summary?.attention_count ?? 0 }}</strong></div><div class="metric-card compact"><span>生成失败</span><strong>{{ summary?.failed_count ?? 0 }}</strong></div><div class="metric-card compact"><span>生成中</span><strong>{{ summary?.generating_count ?? 0 }}</strong></div></div>
    <section v-if="task" class="panel">
      <div class="panel-body task-panel">
        <div class="task-header">
          <b>{{ taskActive ? "当前生成任务" : "最近生成任务" }}</b><span class="muted">{{ task.status === "CANCELLED" ? "已停止" : task.stage }} · {{ task.progress }}%</span><div class="spacer" /><el-button
            v-if="taskActive"
            size="small"
            type="warning"
            plain
            :loading="store.isActionLoading(`task-cancel:${task.id}`)"
            @click="cancel"
          >
            停止任务
          </el-button>
        </div><el-progress :percentage="task.progress" :status="task.status === 'FAILED' ? 'exception' : task.status === 'SUCCEEDED' ? 'success' : task.status === 'CANCELLED' ? 'warning' : undefined" /><small v-if="store.taskMetadata(task, 'document_count')" class="muted">已处理 {{ store.taskMetadata(task, "generated_count") ?? 0 }} / {{ store.taskMetadata(task, "document_count") }} 篇文档</small><el-alert
          v-if="task.error_message"
          :title="task.error_message"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
    </section>
    <section v-if="lastCheck" class="panel">
      <div class="panel-body alerts">
        <el-alert
          :title="lastCheck.publishable ? '检查通过：可上线通过硬校验的待上线属性。' : '检查未通过：请处理以下冲突。'"
          :type="lastCheck.publishable ? 'success' : 'error'"
          :closable="false"
          show-icon
        /><el-alert
          v-for="issue in lastCheck.errors"
          :key="`${issue.code}-${issue.message}`"
          :title="issue.message"
          type="error"
          :closable="false"
        /><el-alert
          v-for="issue in lastCheck.warnings"
          :key="`${issue.code}-${issue.message}`"
          :title="issue.message"
          type="warning"
          :closable="false"
        />
      </div>
    </section>
    <section class="panel">
      <div class="panel-header"><div><h3>文档 Profile 属性</h3><p>待上线值不会影响线上快速命中；RAG 始终可用。</p></div><el-button :disabled="!profiles.some((row) => row.pending_state)" @click="discard">取消全部待上线变更</el-button></div>
      <el-table v-if="profiles.length" :data="profiles" size="small">
        <el-table-column label="文档" min-width="180"><template #default="scope"><b>{{ scope.row.document_name }}</b><small class="muted block">{{ scope.row.document_status }}</small></template></el-table-column><el-table-column label="Profile 状态" width="120"><template #default="scope"><el-tag :type="statusType(scope.row.profile_status)" size="small">{{ statusLabel(scope.row.profile_status) }}</el-tag></template></el-table-column><el-table-column label="当前线上属性" min-width="190"><template #default="scope"><template v-if="scope.row.active_profile?.intents?.length"><div v-for="(intent, index) in scope.row.active_profile.intents" :key="`active-${index}`">{{ intent.canonical_question }}</div></template><span v-else class="muted">暂无线上 Profile（RAG 文档仍可检索）</span></template></el-table-column><el-table-column label="待上线属性" min-width="210"><template #default="scope"><template v-if="scope.row.pending_profile?.intents?.length"><div v-for="(intent, index) in scope.row.pending_profile.intents" :key="`pending-${index}`">{{ intent.canonical_question }}</div><small v-if="scope.row.pending_state === 'NEEDS_ATTENTION' && scope.row.pending_error_message" class="quality-note">{{ scope.row.pending_error_message }}</small></template><span v-else-if="scope.row.pending_error_message" class="muted">{{ scope.row.pending_error_message }}</span><span v-else class="muted">—</span></template></el-table-column><el-table-column label="操作" width="180" align="right">
          <template #default="scope">
            <el-tooltip content="编辑属性" placement="top">
              <el-button
                link
                circle
                type="primary"
                :icon="EditPen"
                aria-label="编辑属性"
                @click="openEditor(scope.row)"
              />
            </el-tooltip>
            <el-tooltip content="重新生成属性" placement="top">
              <el-button
                link
                circle
                :icon="RefreshRight"
                :loading="store.isActionLoading(`document-profile-generate:${scope.row.document_id}`)"
                :disabled="taskActive"
                aria-label="重新生成属性"
                @click="regenerate(scope.row)"
              />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState v-else title="暂无文档" description="上传文档后可在这里生成其待上线 Profile 属性。" />
    </section>
    <el-dialog
      v-model="editorOpen"
      title="编辑待上线 Profile 属性"
      width="680px"
      :close-on-click-modal="false"
    >
      <el-alert
        title="保存后仅更新待上线属性；当前线上属性保持不变，直到点击“上线 Profile 变更”。"
        type="info"
        :closable="false"
        show-icon
        class="editor-alert"
      /><el-form label-position="top">
        <el-form-item label="标准问题"><el-input v-model="form.canonical_question" /></el-form-item><el-form-item label="别名（Alias；每行一项，最多 5 项）">
          <el-input
            :model-value="form.aliases.join('\n')"
            type="textarea"
            :rows="3"
            @update:model-value="form.aliases = lines($event)"
          />
        </el-form-item><el-form-item label="路由摘要"><el-input v-model="form.routing_summary" type="textarea" :rows="3" /></el-form-item><el-form-item><el-switch v-model="form.enabled" active-text="启用快速命中" inactive-text="停用快速命中" /></el-form-item>
      </el-form><template #footer><el-button @click="editorOpen = false">取消</el-button><el-button type="primary" :loading="editing ? store.isActionLoading(`document-profile-edit:${editing.document_id}`) : false" @click="save">保存为待上线属性</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.support-review { display: grid; gap: 16px; }.review-toolbar, .panel-header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }.spacer { flex: 1; }.action-hint { display: block; padding: 0 20px 14px; color: var(--el-text-color-secondary); }.review-metrics { display: grid; grid-template-columns: repeat(5, minmax(110px, 1fr)); gap: 12px; }.metric-card { padding: 14px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-bg-color); }.metric-card span, .muted { color: var(--el-text-color-secondary); }.metric-card strong { display: block; font-size: 24px; margin-top: 4px; }.task-panel, .alerts { display: grid; gap: 8px; }.task-header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }.block { display: block; margin-top: 3px; }.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }.quality-note { display: block; margin-top: 3px; color: var(--warning); }.editor-alert { margin-bottom: 14px; }@media (max-width: 900px) { .review-metrics { grid-template-columns: repeat(2, 1fr); } }
</style>
