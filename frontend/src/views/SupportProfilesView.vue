<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";

import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import { useAdminStore } from "@/composables/adminStore";
import type { ProblemProfile, ProfileDraft, ProfileDraftOperation } from "@/types/admin";

const store = useAdminStore();
const draftDialogOpen = ref(false);
const editingDraft = ref<ProfileDraft | null>(null);
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
const targetProfile = computed(() =>
  store.problemProfiles.find((profile) => profile.id === draftForm.target_profile_id),
);
const operationCounts = computed(() => {
  const drafts = store.selectedRelease?.drafts ?? [];
  return {
    create: drafts.filter((draft) => draft.operation === "CREATE").length,
    update: drafts.filter((draft) => draft.operation === "UPDATE").length,
    none: drafts.filter((draft) => draft.operation === "NONE").length,
  };
});

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

async function rejectDraft(draft: ProfileDraft): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `退回“${draft.document_name}”的草稿？退回后必须修改、重新生成或改为仅参考，才能再次审核。`,
      "退回修改",
      { type: "warning", confirmButtonText: "退回", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.rejectProfileDraft(draft.id);
}

async function regenerateDraft(draft: ProfileDraft): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `重新生成“${draft.document_name}”会覆盖当前未发布内容，是否继续？`,
      "重新生成草稿",
      { type: "warning", confirmButtonText: "重新生成", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.regenerateProfileDraft(draft.id);
}

async function regenerateAll(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      "重新生成会覆盖当前候选中的未发布草稿，人工修改也可能被替换。是否继续？",
      "重新生成全部草稿",
      { type: "warning", confirmButtonText: "重新生成", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.generateProfileDrafts();
}

async function publishRelease(): Promise<void> {
  const release = store.selectedRelease;
  if (!release) return;
  const warnings = release.validation_result?.warnings?.length ?? 0;
  try {
    await ElMessageBox.confirm(
      `将为“${store.selectedKnowledge?.name ?? "当前知识库"}”发布 Index v${release.index_version}：新增 ${operationCounts.value.create}、更新 ${operationCounts.value.update}、仅参考 ${operationCounts.value.none}，质量提示 ${warnings} 项。发布后新请求将使用该版本。`,
      "确认原子发布",
      { type: warnings ? "warning" : "success", confirmButtonText: "确认发布", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  await store.publishKnowledgeRelease();
}

async function loadWorkspace(): Promise<void> {
  await store.run(async () => {
    await Promise.all([store.loadProblemProfiles(), store.loadReleaseWorkspace()]);
  });
}

watch(
  () => store.selectedKnowledgeId,
  () => void loadWorkspace(),
);

onMounted(() => void loadWorkspace());
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
          <el-button @click="loadWorkspace">刷新</el-button>
          <template v-if="store.selectedRelease">
            <el-button :loading="store.loading" @click="regenerateAll">
              重新生成草稿
            </el-button>
            <el-button :loading="store.loading" @click="store.validateKnowledgeRelease()">
              发布前验证
            </el-button>
            <el-button
              type="primary"
              :loading="store.loading"
              :disabled="store.selectedRelease.status !== 'READY_TO_PUBLISH'"
              @click="publishRelease"
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
          <el-table-column label="生成" width="105">
            <template #default="scope"><StatusBadge :status="scope.row.generation_source" /></template>
          </el-table-column>
          <el-table-column label="置信度" width="90">
            <template #default="scope">{{ Math.round(scope.row.confidence * 100) }}%</template>
          </el-table-column>
          <el-table-column label="审核" width="110">
            <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
          </el-table-column>
          <el-table-column label="操作" width="260" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openDraft(scope.row)">编辑</el-button>
              <el-button
                v-if="scope.row.status === 'DRAFT'"
                link
                type="success"
                @click="approveDraft(scope.row)"
              >
                审核通过
              </el-button>
              <el-button
                v-if="scope.row.status === 'DRAFT'"
                link
                type="danger"
                @click="rejectDraft(scope.row)"
              >
                退回
              </el-button>
              <el-button link @click="regenerateDraft(scope.row)">重新生成</el-button>
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
          <h3>线上问题与完整文档映射</h3>
          <span class="sub">此处只读展示当前线上配置；修改必须通过上方候选草稿审核发布。</span>
        </div>
        <StatusBadge status="ACTIVE" />
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
      </el-table>
      <EmptyState
        v-else
        title="暂无问题文档配置"
        description="上传文档后审核自动生成的 Profile 草稿，发布成功后会出现在这里。"
      />
    </section>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>发布历史</h3>
          <span class="sub">已发布、已取消和当前候选均保留记录；只有当前开放候选可以操作。</span>
        </div>
      </header>
      <el-table v-if="store.knowledgeReleases.length" :data="store.knowledgeReleases">
        <el-table-column label="索引版本" width="120">
          <template #default="scope">Index v{{ scope.row.index_version }}</template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="草稿" width="130">
          <template #default="scope">{{ scope.row.approved_count }}/{{ scope.row.draft_count }} 已审核</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="发布时间" min-width="180">
          <template #default="scope">{{ store.formatDate(scope.row.published_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState v-else title="暂无发布记录" description="第一个候选构建提交后会在这里出现。" />
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

    <el-dialog v-model="draftDialogOpen" title="审核 Profile 草稿" width="680px">
      <div v-if="editingDraft" class="dialog-form">
        <div class="draft-origin">
          <div><span>生成来源</span><StatusBadge :status="editingDraft.generation_source" /></div>
          <div><span>置信度</span><strong>{{ Math.round(editingDraft.confidence * 100) }}%</strong></div>
          <div><span>当前状态</span><StatusBadge :status="editingDraft.status" /></div>
        </div>
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
        <div v-if="draftForm.operation === 'UPDATE' && targetProfile" class="profile-compare">
          <h4>当前线上值</h4>
          <dl>
            <dt>问题编号</dt><dd>{{ targetProfile.problem_code }}</dd>
            <dt>标准问题</dt><dd>{{ targetProfile.canonical_question }}</dd>
            <dt>Alias</dt><dd>{{ targetProfile.aliases.join("、") || "—" }}</dd>
            <dt>关键词</dt><dd>{{ targetProfile.keywords.join("、") || "—" }}</dd>
            <dt>文档</dt><dd>{{ documentNames.get(targetProfile.document_id) ?? targetProfile.document_id }}</dd>
          </dl>
          <p>下方为候选值；保存修改后需要重新审核。</p>
        </div>
        <template v-if="draftForm.operation !== 'NONE'">
          <div class="dialog-field"><label>问题编号</label><el-input v-model="draftForm.problem_code" /></div>
          <div class="dialog-field"><label>标准问题</label><el-input v-model="draftForm.canonical_question" /></div>
          <div class="dialog-field">
            <label>Alias</label>
            <el-select
              v-model="draftForm.aliases"
              multiple
              filterable
              allow-create
              style="width: 100%"
            />
          </div>
          <div class="dialog-field">
            <label>区分关键词</label>
            <el-select
              v-model="draftForm.keywords"
              multiple
              filterable
              allow-create
              style="width: 100%"
            />
          </div>
          <div class="dialog-field"><label>产品</label><el-input v-model="draftForm.product_name" /></div>
          <div class="dialog-field">
            <label>型号</label>
            <el-select
              v-model="draftForm.model_names"
              multiple
              filterable
              allow-create
              style="width: 100%"
            />
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
.draft-origin { display: flex; flex-wrap: wrap; gap: 18px; padding: 12px; border-radius: var(--radius-md); background: var(--bg-subtle); }
.draft-origin > div { display: flex; align-items: center; gap: 8px; }
.draft-origin span, .profile-compare dt, .profile-compare p { color: var(--text-tertiary); font-size: 12px; }
.profile-compare { padding: 14px; border: 1px solid var(--border-default); border-radius: var(--radius-md); background: var(--bg-subtle); }
.profile-compare h4 { margin-bottom: 10px; font-size: 13px; }
.profile-compare dl { display: grid; grid-template-columns: 90px 1fr; gap: 7px 12px; margin: 0; }
.profile-compare dd { margin: 0; color: var(--text-secondary); overflow-wrap: anywhere; }
.profile-compare p { margin-top: 10px; }
@media (max-width: 900px) { .readiness-strip { grid-template-columns: 1fr 1fr 1fr; } .readiness-strip p { grid-column: 1 / -1; } }
</style>
