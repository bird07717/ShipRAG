<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import { useAdminStore } from "@/composables/adminStore";
import type { ProblemProfile, ProfileDraft, ProfileDraftOperation } from "@/types/admin";

const store = useAdminStore();
const editingProfile = ref<ProblemProfile | null>(null);
const dialogOpen = ref(false);
const draftDialogOpen = ref(false);
const editingDraft = ref<ProfileDraft | null>(null);
const form = reactive({
  document_id: "",
  problem_code: "",
  canonical_question: "",
  aliases: [] as string[],
  keywords: [] as string[],
  product_name: "",
  model_names: [] as string[],
  task_type: "",
  enabled: true,
});
const draftForm = reactive({
  operation: "CREATE" as ProfileDraftOperation,
  target_profile_id: "",
  problem_code: "",
  canonical_question: "",
  aliases: [] as string[],
  keywords: [] as string[],
  product_name: "",
  model_names: [] as string[],
  task_type: "",
  enabled: true,
});

const readyCount = computed(
  () => store.problemProfiles.filter((profile) => profile.support_ready).length,
);
const documentNames = computed(
  () => new Map(store.documents.map((document) => [document.id, document.display_name])),
);

const reasonLabels: Record<string, string> = {
  PROFILE_DISABLED: "Profile 未启用",
  DOCUMENT_NOT_STORED: "关联文档不存在",
  KNOWLEDGE_BASE_NOT_READY: "知识库没有 Active Index",
  DOCUMENT_NOT_PUBLISHED: "文档未发布到当前 Active Index",
  IMAGE_ASSET_MISSING: "文档图片资源不完整",
};

function readinessText(profile: ProblemProfile): string {
  return profile.readiness_reasons.map((reason) => reasonLabels[reason] ?? reason).join("；");
}

function openProfile(profile?: ProblemProfile): void {
  editingProfile.value = profile ?? null;
  form.document_id = profile?.document_id ?? store.documents[0]?.id ?? "";
  form.problem_code = profile?.problem_code ?? "";
  form.canonical_question = profile?.canonical_question ?? "";
  form.aliases = [...(profile?.aliases ?? [])];
  form.keywords = [...(profile?.keywords ?? [])];
  form.product_name = profile?.product_name ?? "";
  form.model_names = [...(profile?.model_names ?? [])];
  form.task_type = profile?.task_type ?? "";
  form.enabled = profile?.enabled ?? true;
  dialogOpen.value = true;
}

async function saveProfile(): Promise<void> {
  await store.saveProblemProfile({
    document_id: form.document_id,
    problem_code: form.problem_code.trim(),
    canonical_question: form.canonical_question.trim(),
    aliases: form.aliases.map((item) => item.trim()).filter(Boolean),
    keywords: form.keywords.map((item) => item.trim()).filter(Boolean),
    product_name: form.product_name.trim() || null,
    model_names: form.model_names.map((item) => item.trim()).filter(Boolean),
    task_type: form.task_type.trim() || null,
    enabled: form.enabled,
  });
  if (!store.error) dialogOpen.value = false;
}

function openDraft(draft: ProfileDraft): void {
  editingDraft.value = draft;
  draftForm.operation = draft.operation;
  draftForm.target_profile_id = draft.target_profile_id ?? "";
  draftForm.problem_code = draft.problem_code;
  draftForm.canonical_question = draft.canonical_question;
  draftForm.aliases = [...draft.aliases];
  draftForm.keywords = [...draft.keywords];
  draftForm.product_name = draft.product_name ?? "";
  draftForm.model_names = [...draft.model_names];
  draftForm.task_type = draft.task_type ?? "";
  draftForm.enabled = draft.enabled;
  draftDialogOpen.value = true;
}

async function saveDraft(): Promise<void> {
  const draft = editingDraft.value;
  if (!draft) return;
  await store.saveProfileDraft(draft.id, {
    operation: draftForm.operation,
    target_profile_id: draftForm.target_profile_id || null,
    problem_code: draftForm.problem_code.trim(),
    canonical_question: draftForm.canonical_question.trim(),
    aliases: draftForm.aliases.map((item) => item.trim()).filter(Boolean),
    keywords: draftForm.keywords.map((item) => item.trim()).filter(Boolean),
    product_name: draftForm.product_name.trim() || null,
    model_names: draftForm.model_names.map((item) => item.trim()).filter(Boolean),
    task_type: draftForm.task_type.trim() || null,
    enabled: draftForm.enabled,
    status: "DRAFT",
  });
  if (!store.error) draftDialogOpen.value = false;
}

async function approveDraft(draft: ProfileDraft): Promise<void> {
  await store.saveProfileDraft(draft.id, { status: "APPROVED" });
}

watch(
  () => store.selectedKnowledgeId,
  () =>
    void Promise.all([store.loadProblemProfiles(), store.loadReleaseWorkspace()]).catch(
      () => undefined,
    ),
);

onMounted(
  () =>
    void Promise.all([store.loadProblemProfiles(), store.loadReleaseWorkspace()]).catch(
      () => undefined,
    ),
);
</script>

<template>
  <div class="support-profiles">
    <section class="readiness-strip">
      <div><span>问题文档</span><strong>{{ store.problemProfiles.length }}</strong></div>
      <div><span>可用于问答</span><strong>{{ readyCount }}</strong></div>
      <div><span>需要处理</span><strong>{{ store.problemProfiles.length - readyCount }}</strong></div>
      <p>Alias 是可选的高频问法优化项；文档存在、已发布且图片完整即可用于正式问答。</p>
    </section>

    <section class="panel release-panel">
      <header class="panel-header">
        <div>
          <h3>发布候选</h3>
          <span class="sub">候选索引和 Profile 草稿审核通过后同时发布，审核过程不影响线上问答。</span>
        </div>
        <div class="release-actions">
          <el-button @click="store.loadReleaseWorkspace()">刷新</el-button>
          <template v-if="store.selectedRelease">
            <el-button :loading="store.loading" @click="store.generateProfileDrafts()">
              重新生成草稿
            </el-button>
            <el-button :loading="store.loading" @click="store.validateKnowledgeRelease()">
              发布前验证
            </el-button>
            <el-button
              type="primary"
              :loading="store.loading"
              :disabled="store.selectedRelease.status !== 'READY_TO_PUBLISH'"
              @click="store.publishKnowledgeRelease()"
            >
              原子发布
            </el-button>
          </template>
        </div>
      </header>

      <template v-if="store.selectedRelease">
        <div class="release-summary">
          <StatusBadge :status="store.selectedRelease.status" />
          <span>Index v{{ store.selectedRelease.index_version }}</span>
          <span class="mono">{{ store.selectedRelease.index_id }}</span>
          <span>{{ store.selectedRelease.approved_count }}/{{ store.selectedRelease.draft_count }} 已审核</span>
        </div>
        <el-alert
          v-if="store.selectedRelease.generation_error"
          type="warning"
          :title="store.selectedRelease.generation_error"
          :closable="false"
          show-icon
        />
        <el-table v-if="store.selectedRelease.drafts?.length" :data="store.selectedRelease.drafts">
          <el-table-column prop="document_name" label="变更文档" min-width="220" />
          <el-table-column label="建议" width="100">
            <template #default="scope"><StatusBadge :status="scope.row.operation" /></template>
          </el-table-column>
          <el-table-column prop="problem_code" label="问题编号" min-width="170" />
          <el-table-column prop="canonical_question" label="标准问题" min-width="230" />
          <el-table-column label="生成" width="100">
            <template #default="scope">{{ scope.row.generation_source }}</template>
          </el-table-column>
          <el-table-column label="审核" width="110">
            <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column label="操作" width="210" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openDraft(scope.row)">编辑</el-button>
              <el-button
                v-if="scope.row.status !== 'APPROVED'"
                link
                type="success"
                @click="approveDraft(scope.row)"
              >
                审核通过
              </el-button>
              <el-button link @click="store.regenerateProfileDraft(scope.row.id)">重新生成</el-button>
            </template>
          </el-table-column>
        </el-table>
        <EmptyState
          v-else
          title="正在生成 Profile 草稿"
          description="索引构建完成后会自动生成；也可以点击“重新生成草稿”。"
        />
        <div
          v-if="store.selectedRelease.validation_result?.errors?.length || store.selectedRelease.validation_result?.warnings?.length"
          class="validation-grid"
        >
          <div v-if="store.selectedRelease.validation_result?.errors?.length" class="validation-errors">
            <b>必须处理</b>
            <p v-for="item in store.selectedRelease.validation_result.errors" :key="`${item.code}-${item.message}`">
              {{ item.message }} <span class="mono">{{ item.code }}</span>
            </p>
          </div>
          <div v-if="store.selectedRelease.validation_result?.warnings?.length" class="validation-warnings">
            <b>质量提示</b>
            <p v-for="item in store.selectedRelease.validation_result.warnings" :key="`${item.code}-${item.message}`">
              {{ item.message }} <span class="mono">{{ item.code }}</span>
            </p>
          </div>
        </div>
      </template>
      <EmptyState
        v-else
        title="没有待发布候选"
        description="上传文档后会构建 READY 候选索引，并在这里生成可审核的 Profile 草稿。"
      />
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>问题与完整文档映射</h3>
          <span class="sub">每篇文档描述一个完整问题，首轮命中后按原顺序返回全文和图片。</span>
        </div>
        <el-button
          type="primary"
          :disabled="!store.selectedKnowledgeId || !store.documents.length"
          @click="openProfile()"
        >
          新建问题文档
        </el-button>
      </header>

      <el-table v-if="store.problemProfiles.length" :data="store.problemProfiles">
        <el-table-column prop="problem_code" label="问题编号" min-width="170" />
        <el-table-column prop="canonical_question" label="标准问题" min-width="240" />
        <el-table-column label="关联文档" min-width="220">
          <template #default="scope">
            {{ documentNames.get(scope.row.document_id) ?? scope.row.document_id }}
          </template>
        </el-table-column>
        <el-table-column label="可选 Alias" min-width="190">
          <template #default="scope">{{ scope.row.aliases.join("、") || "—" }}</template>
        </el-table-column>
        <el-table-column label="问答状态" min-width="210">
          <template #default="scope">
            <StatusBadge :status="scope.row.support_ready ? 'READY' : 'NOT_READY'" />
            <div v-if="!scope.row.support_ready" class="reason">
              {{ readinessText(scope.row) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="scope">
            <el-button link type="primary" @click="openProfile(scope.row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        title="暂无问题文档配置"
        description="先上传并发布文档，再为每篇文档填写问题编号和标准问题。Alias 可以留空。"
      />
    </section>

    <section v-if="store.profileFeedback.length" class="panel">
      <header class="panel-header">
        <div>
          <h3>线上反馈建议</h3>
          <span class="sub">近 30 天重复 NO_MATCH 问题，只生成建议，不自动修改线上 Profile。</span>
        </div>
      </header>
      <el-table :data="store.profileFeedback">
        <el-table-column prop="question" label="未命中问题" min-width="300" />
        <el-table-column prop="occurrences" label="次数" width="90" />
        <el-table-column prop="recommendation" label="建议" min-width="240" />
        <el-table-column label="最近出现" width="180">
          <template #default="scope">{{ store.formatDate(scope.row.last_seen) }}</template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialogOpen" :title="editingProfile ? '编辑问题文档' : '新建问题文档'" width="600px">
      <div class="dialog-form">
        <div class="dialog-field">
          <label>完整技术文档</label>
          <el-select v-model="form.document_id" style="width: 100%">
            <el-option
              v-for="document in store.documents"
              :key="document.id"
              :label="document.display_name"
              :value="document.id"
            />
          </el-select>
        </div>
        <div class="dialog-field">
          <label>问题编号</label>
          <el-input v-model="form.problem_code" placeholder="例如 PDC_UPDATE_UPGRADE" />
        </div>
        <div class="dialog-field">
          <label>标准问题</label>
          <el-input v-model="form.canonical_question" placeholder="例如 如何通过U盘升级PDC？" />
        </div>
        <div class="dialog-field">
          <label>常用问法 Alias（可选）</label>
          <el-select
            v-model="form.aliases"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="仅添加已经确认的高频问法，可以留空"
            style="width: 100%"
          />
        </div>
        <div class="dialog-field">
          <label>辅助关键词（可选）</label>
          <el-select
            v-model="form.keywords"
            multiple
            filterable
            allow-create
            style="width: 100%"
          />
        </div>
        <div class="dialog-field"><label>产品名称（可选）</label><el-input v-model="form.product_name" /></div>
        <div class="dialog-field">
          <label>型号（可选）</label>
          <el-select
            v-model="form.model_names"
            multiple
            filterable
            allow-create
            style="width: 100%"
          />
        </div>
        <div class="dialog-field"><label>任务类型（可选）</label><el-input v-model="form.task_type" /></div>
        <div class="dialog-field"><label>启用</label><el-switch v-model="form.enabled" /></div>
      </div>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.loading"
          :disabled="!form.document_id || !form.problem_code.trim() || !form.canonical_question.trim()"
          @click="saveProfile"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="draftDialogOpen" title="审核 Profile 草稿" width="680px">
      <div v-if="editingDraft" class="dialog-form">
        <el-alert
          v-if="editingDraft.reasoning"
          :title="editingDraft.reasoning"
          type="info"
          :closable="false"
        />
        <div class="dialog-field">
          <label>处理方式</label>
          <el-radio-group v-model="draftForm.operation">
            <el-radio-button value="CREATE">新增问题</el-radio-button>
            <el-radio-button value="UPDATE">替换已有问题文档</el-radio-button>
            <el-radio-button value="NONE">仅作为参考文档</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="draftForm.operation === 'UPDATE'" class="dialog-field">
          <label>要替换的线上 Profile</label>
          <el-select v-model="draftForm.target_profile_id" style="width: 100%">
            <el-option
              v-for="profile in store.problemProfiles"
              :key="profile.id"
              :label="`${profile.problem_code} · ${profile.canonical_question}`"
              :value="profile.id"
            />
          </el-select>
        </div>
        <template v-if="draftForm.operation !== 'NONE'">
          <div class="dialog-field"><label>问题编号</label><el-input v-model="draftForm.problem_code" /></div>
          <div class="dialog-field"><label>标准问题</label><el-input v-model="draftForm.canonical_question" /></div>
          <div class="dialog-field">
            <label>Alias</label>
            <el-select v-model="draftForm.aliases" multiple filterable allow-create style="width: 100%" />
          </div>
          <div class="dialog-field">
            <label>区分关键词</label>
            <el-select v-model="draftForm.keywords" multiple filterable allow-create style="width: 100%" />
          </div>
          <div class="dialog-field"><label>产品</label><el-input v-model="draftForm.product_name" /></div>
          <div class="dialog-field">
            <label>型号</label>
            <el-select v-model="draftForm.model_names" multiple filterable allow-create style="width: 100%" />
          </div>
          <div class="dialog-field"><label>任务类型</label><el-input v-model="draftForm.task_type" /></div>
          <div class="dialog-field"><label>发布后启用</label><el-switch v-model="draftForm.enabled" /></div>
        </template>
      </div>
      <template #footer>
        <el-button @click="draftDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="store.loading" @click="saveDraft">保存草稿</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.support-profiles { display: grid; gap: var(--space-content-gap); }
.release-panel { display: grid; gap: 14px; }
.release-actions, .release-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.release-summary { padding: 0 18px; color: var(--text-secondary); font-size: 12px; }
.validation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 0 18px 18px; }
.validation-grid > div { padding: 12px; border: 1px solid var(--border-default); border-radius: var(--radius-md); }
.validation-grid p { margin: 6px 0 0; color: var(--text-secondary); font-size: 12px; }
.validation-errors { border-color: var(--danger-border, var(--border-default)) !important; }
.validation-warnings { border-color: var(--warning-border, var(--border-default)) !important; }
.readiness-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(120px, 180px)) minmax(260px, 1fr);
  gap: 16px;
  align-items: center;
  padding: 16px 20px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-panel);
  background: var(--bg-surface);
}
.readiness-strip div { display: grid; gap: 3px; }
.readiness-strip span, .readiness-strip p, .reason { color: var(--text-tertiary); font-size: 12px; }
.readiness-strip strong { color: var(--text-primary); font-size: 24px; }
.readiness-strip p { margin: 0; line-height: 1.6; }
.reason { margin-top: 5px; line-height: 1.45; }
@media (max-width: 900px) { .readiness-strip { grid-template-columns: 1fr 1fr 1fr; } .readiness-strip p { grid-column: 1 / -1; } }
</style>
