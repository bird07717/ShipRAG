<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import EmptyState from "@/components/ui/EmptyState.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import { useAdminStore } from "@/composables/adminStore";
import type { ProblemProfile } from "@/types/admin";

const store = useAdminStore();
const editingProfile = ref<ProblemProfile | null>(null);
const dialogOpen = ref(false);
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

watch(
  () => store.selectedKnowledgeId,
  () => void store.loadProblemProfiles().catch(() => undefined),
);

onMounted(() => void store.loadProblemProfiles().catch(() => undefined));
</script>

<template>
  <div class="support-profiles">
    <section class="readiness-strip">
      <div><span>问题文档</span><strong>{{ store.problemProfiles.length }}</strong></div>
      <div><span>可用于问答</span><strong>{{ readyCount }}</strong></div>
      <div><span>需要处理</span><strong>{{ store.problemProfiles.length - readyCount }}</strong></div>
      <p>Alias 是可选的高频问法优化项；文档存在、已发布且图片完整即可用于正式问答。</p>
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
  </div>
</template>

<style scoped>
.support-profiles { display: grid; gap: var(--space-content-gap); }
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
