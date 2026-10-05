<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ChatDotRound, Clock, Files, Warning } from "@element-plus/icons-vue";
import { adminApi } from "@/api/admin";
import { useAdminStore } from "@/composables/adminStore";
import MetricCard from "@/components/ui/MetricCard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import ListSkeleton from "@/components/ui/ListSkeleton.vue";
import TrendChart, { type TrendPoint } from "@/components/ui/TrendChart.vue";
import type { AssistantMetrics, KnowledgeAssistantCheck, MetricsOverview, MetricsTimeseriesPoint, TraceSummary } from "@/types/admin";

const store = useAdminStore();
const overview = ref<MetricsOverview | null>(null);
const assistantMetrics = ref<AssistantMetrics | null>(null);
const readiness = ref<KnowledgeAssistantCheck | null>(null);
const timeseries = ref<MetricsTimeseriesPoint[]>([]);
const unconfirmedTraces = ref<TraceSummary[]>([]);
const metricsError = ref("");
const metricsLoading = ref(false);
const refreshing = ref(false);
const lastRefreshedAt = ref<Date | null>(null);
const drilldown = ref<{ label: string; from: string; to: string } | null>(null);
const drilldownTraces = ref<TraceSummary[]>([]);
const drilldownLoading = ref(false);
const drilldownError = ref("");
let autoRefreshTimer: ReturnType<typeof setInterval> | undefined;
let active = true;
let loadGeneration = 0;
let drilldownGeneration = 0;
let lastLoadedKb: string | undefined;
const QUALITY_WINDOW_DAYS = 7;
const QUALITY_ENGINE = "RAG" as const;
const QUALITY_SOURCE = "ASSISTANT_API" as const;

const knowledge = computed(() => store.knowledgeBases.find((item) => item.id === store.selectedKnowledgeId));
const scopeLabel = computed(() => knowledge.value?.name || (store.selectedKnowledgeId ? "当前知识库" : "全部知识库"));
const lastRefreshedLabel = computed(() => lastRefreshedAt.value?.toLocaleTimeString("zh-CN", { hour12: false }) || "尚未加载");
const percent = (value: number, denominator: number) => denominator > 0 ? (value * 100).toFixed(1) + "%" : "—";
const latency = (value: number, samples: number) => samples > 0 ? value.toLocaleString() + " ms" : "—";
const assistantSource = computed(() => assistantMetrics.value
  ? assistantMetrics.value.sources.find((item) => item.source === "ASSISTANT_API")
    ?? { source: "ASSISTANT_API", turns: 0, users: 0, conversations: 0, failed: 0, error_rate: 0 }
  : undefined);
const directAnswerCount = computed(() => (overview.value?.response_type_counts.ANSWERED ?? 0) + (overview.value?.response_type_counts.DOC_DELIVERED ?? 0));
const availabilityLabel = computed(() => {
  if (!store.selectedKnowledgeId) return "请选择知识库查看上线状态";
  if (!readiness.value) return "上线状态待确认";
  if (!readiness.value.assistant_online) return "尚未对外上线";
  return readiness.value.ready ? "已上线 · 可接受业务提问" : "已上线 · 暂不可用";
});
const availabilityDescription = computed(() => {
  if (!readiness.value) return "索引就绪不等于已上线；需要同时检查模型、文档理解及发布状态。";
  if (!readiness.value.ready) return "仍有运行条件未满足，请打开上线检查清单处理。";
  return readiness.value.assistant_online ? "当前运行条件已通过。回答效果仍需人工复核。" : "运行条件已通过；保存推荐问题、试问后可确认上线。";
});
const blockedCount = computed(() => readiness.value?.checks?.filter((item) => item.status === "BLOCKED").length ?? readiness.value?.blockers.length ?? 0);
const recentTraces = computed(() => store.traces.filter((item) => !store.selectedKnowledgeId || item.kb_id === store.selectedKnowledgeId).slice(0, 5));
const bucketSpanMs = computed(() => {
  const [first, second] = timeseries.value;
  const span = first && second ? new Date(second.bucket).getTime() - new Date(first.bucket).getTime() : 0;
  return span > 0 ? span : 86_400_000;
});
const chartPoints = computed<TrendPoint[]>(() => timeseries.value.map((point) => ({
  bucket: point.bucket,
  label: bucketSpanMs.value <= 3_600_000 ? point.bucket.slice(5, 13).replace("T", " ") : point.bucket.slice(5, 10),
  queries: point.queries, needsInformationRate: point.needs_information_rate,
})));

function openPublication() {
  if (store.selectedKnowledgeId) store.navigate("knowledge", "id=" + encodeURIComponent(store.selectedKnowledgeId));
  else store.navigate("knowledge");
}

function openFailedTurns() {
  if (!overview.value || !store.hasServiceCredential) return;
  const query = new URLSearchParams({
    tab: "failed-turns", engine: QUALITY_ENGINE, source: QUALITY_SOURCE,
    from: overview.value.range.from, to: overview.value.range.to,
  });
  store.navigate("logs", query.toString());
}

async function loadMetrics() {
  if (!active || !store.hasServiceCredential) return;
  const generation = ++loadGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  metricsLoading.value = true;
  metricsError.value = "";
  if (lastLoadedKb !== knowledgeId) {
    overview.value = null; assistantMetrics.value = null; readiness.value = null;
    timeseries.value = []; unconfirmedTraces.value = []; lastRefreshedAt.value = null;
    drilldownGeneration++; drilldown.value = null;
  }
  lastLoadedKb = knowledgeId;
  const to = new Date();
  const from = new Date(to.getTime() - QUALITY_WINDOW_DAYS * 86_400_000);
  const qualityParams = { knowledgeId: knowledgeId || undefined, from: from.toISOString(), to: to.toISOString(), engine: QUALITY_ENGINE, source: QUALITY_SOURCE };
  const results = await Promise.allSettled([
    adminApi.getMetricsOverview(qualityParams),
    adminApi.getMetricsTimeseries(qualityParams),
    adminApi.listUnconfirmedTraces(knowledgeId || undefined, qualityParams),
    adminApi.getAssistantMetrics({ knowledgeId: knowledgeId || undefined, days: QUALITY_WINDOW_DAYS }),
    knowledgeId ? adminApi.getKnowledgeAssistant(knowledgeId) : Promise.resolve(null),
  ]);
  if (!active || generation !== loadGeneration || store.selectedKnowledgeId !== knowledgeId) return;
  const [summary, trend, questions, assistant, check] = results;
  const summaryValid = summary?.status === "fulfilled" && typeof summary.value?.total_traces === "number";
  const trendValid = trend?.status === "fulfilled" && Array.isArray(trend.value);
  const questionsValid = questions?.status === "fulfilled" && Array.isArray(questions.value);
  const assistantValid = assistant?.status === "fulfilled" && Array.isArray(assistant.value?.sources);
  const checkValid = check?.status === "fulfilled" && (!knowledgeId || (check.value?.knowledge_id === knowledgeId && typeof check.value.ready === "boolean"));
  if (summaryValid) overview.value = summary.value;
  if (trendValid) timeseries.value = trend.value;
  if (questionsValid) unconfirmedTraces.value = questions.value;
  if (assistantValid) assistantMetrics.value = assistant.value;
  if (checkValid && knowledgeId) readiness.value = check.value;
  else readiness.value = null;
  if (![summaryValid, trendValid, questionsValid, assistantValid, checkValid].every(Boolean)) {
    metricsError.value = "部分质量指标暂时无法加载；已显示的数据可能仍为上次结果，请重试。";
  } else lastRefreshedAt.value = new Date();
  metricsLoading.value = false;
}
function autoRefresh() { if (store.hasServiceCredential && !metricsLoading.value) void loadMetrics(); }
async function refreshDashboard() {
  if (!store.hasServiceCredential || refreshing.value) return;
  refreshing.value = true;
  try {
    await Promise.all([store.refreshOperationalState(), store.loadSystemInfo()]);
    await loadMetrics();
  }
  catch (cause) { if (active) metricsError.value = cause instanceof Error ? cause.message : "刷新失败，请重试"; }
  finally { if (active) refreshing.value = false; }
}
async function openBucketDrilldown(point: TrendPoint) {
  const generation = ++drilldownGeneration;
  const knowledgeId = store.selectedKnowledgeId;
  const bucketEnd = new Date(new Date(point.bucket).getTime() + bucketSpanMs.value).toISOString();
  drilldown.value = { label: point.label, from: point.bucket, to: bucketEnd };
  drilldownTraces.value = []; drilldownLoading.value = true; drilldownError.value = "";
  try {
    const items = await adminApi.listTraces(knowledgeId || undefined, { from: point.bucket, to: bucketEnd, engine: QUALITY_ENGINE, source: QUALITY_SOURCE, limit: 100 });
    if (active && generation === drilldownGeneration && store.selectedKnowledgeId === knowledgeId) drilldownTraces.value = items;
  } catch (cause) { if (active && generation === drilldownGeneration) drilldownError.value = cause instanceof Error ? cause.message : "链路加载失败"; }
  finally { if (active && generation === drilldownGeneration) drilldownLoading.value = false; }
}
onMounted(() => { void loadMetrics(); autoRefreshTimer = setInterval(autoRefresh, 60_000); });
onUnmounted(() => { active = false; loadGeneration++; drilldownGeneration++; clearInterval(autoRefreshTimer); });
watch(() => store.selectedKnowledgeId, () => void loadMetrics());
watch(() => store.hasServiceCredential, (available) => {
  if (available) void loadMetrics();
  else { loadGeneration++; metricsLoading.value = false; }
});
</script>

<template>
  <div class="dashboard">
    <section class="panel dashboard-header">
      <header class="panel-header">
        <div><h3>运行概览</h3><span class="sub">{{ scopeLabel }} · 近 7 天 · 上次完整刷新 {{ lastRefreshedLabel }}</span></div>
        <div class="scope-actions">
          <el-select
            v-model="store.selectedKnowledgeId"
            aria-label="统计知识库"
            placeholder="全部知识库"
            @change="store.selectKnowledge()"
          >
            <el-option label="全部知识库" value="" />
            <el-option
              v-for="kb in store.knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <el-button :loading="refreshing" :disabled="!store.hasServiceCredential" @click="refreshDashboard">刷新状态</el-button>
        </div>
      </header>
      <p class="scope-note">仅统计业务端 Assistant，不混入内部试问。每 60 秒刷新；直接回答率不是答案正确率。</p>
    </section>

    <el-alert
      v-if="metricsError"
      :title="metricsError"
      type="warning"
      :closable="false"
      show-icon
    />
    <ListSkeleton v-if="metricsLoading && !overview && !assistantMetrics" variant="metrics" />

    <section class="status-layout">
      <article class="panel availability-panel">
        <span class="eyebrow">能否对外提供服务</span>
        <h3 :class="{ 'status-blocked': readiness?.assistant_online && !readiness.ready }">{{ availabilityLabel }}</h3>
        <p>{{ availabilityDescription }}</p>
        <div class="status-meta"><span>{{ knowledge?.document_count ?? '—' }} 份文档</span><span>{{ store.activeIndex ? '当前索引 v' + store.activeIndex.version : '未确认活动索引' }}</span></div>
        <el-button type="primary" plain @click="openPublication">打开上线检查清单</el-button>
      </article>
      <article class="panel attention-panel">
        <span class="eyebrow">现在需要处理什么</span>
        <button
          v-if="blockedCount"
          type="button"
          class="attention-row"
          @click="openPublication"
        >
          <span>上线运行条件待处理</span><b>{{ blockedCount }} 项 →</b>
        </button>
        <button
          v-if="knowledge?.rebuild_required"
          type="button"
          class="attention-row"
          @click="store.navigate('indexes')"
        >
          <span>文档已变更，需要重建索引</span><b>去处理 →</b>
        </button>
        <button
          v-if="store.buildingCount"
          type="button"
          class="attention-row"
          @click="store.navigate('indexes')"
        >
          <span>索引任务正在处理</span><b>{{ store.buildingCount }} 个 →</b>
        </button>
        <button
          v-if="overview && overview.failed > 0"
          type="button"
          class="attention-row"
          @click="openFailedTurns"
        >
          <span>近 7 天技术请求执行失败</span><b>{{ overview.failed }} 次 →</b>
        </button>
        <p v-if="!blockedCount && !knowledge?.rebuild_required && !store.buildingCount && !overview?.failed" class="muted">{{ overview && readiness ? '当前未发现上述运行待办；这不代表所有回答都正确。' : '数据或知识库状态未确认，暂不能判断是否有运行待办。' }}</p>
      </article>
    </section>

    <section class="usage-section">
      <header class="section-heading"><div><h3>近期使用情况</h3><p>外部请求含寒暄和对话；技术回答与耗时使用单独的技术样本。</p></div><el-button text @click="store.navigate('logs', 'tab=assistant-answers')">查看回答与优化 →</el-button></header>
      <div class="metric-grid">
        <MetricCard
          label="外部请求轮次"
          :value="assistantSource ? assistantSource.turns.toLocaleString() : '—'"
          :context="assistantSource ? '外部用户 ' + assistantSource.users + ' · 会话 ' + assistantSource.conversations : '调用数据未加载'"
          :icon="ChatDotRound"
        />
        <MetricCard
          label="直接回答率"
          :value="overview ? percent(overview.direct_answer_rate, overview.in_scope_traces) : '—'"
          :context="overview ? '直接回答 ' + directAnswerCount + ' / 范围内技术回答 ' + overview.in_scope_traces : '技术回答数据未加载'"
          :icon="Files"
        />
        <MetricCard
          label="外部执行失败率"
          :value="assistantSource ? percent(assistantSource.error_rate, assistantSource.turns) : '—'"
          :context="assistantSource ? '执行失败 ' + assistantSource.failed + ' / 外部请求 ' + assistantSource.turns : '不是答案质量评分'"
          :icon="Warning"
          :tone="assistantSource && assistantSource.error_rate > 0.05 ? 'danger' : 'default'"
        />
        <MetricCard
          label="技术回答 P95 耗时"
          :value="overview ? latency(overview.latency.p95_ms, overview.latency.samples) : '—'"
          :context="overview ? '95% 的技术样本不超过此时长 · 样本 ' + overview.latency.samples : '不含寒暄、无需检索的对话'"
          :icon="Clock"
          :tone="overview && overview.latency.p95_ms > 10000 ? 'warning' : 'default'"
        />
      </div>
      <p v-if="assistantSource?.turns === 0" class="empty-note">近 7 天暂无外部调用；无样本的比例和耗时显示“—”，不表示零错误或零耗时。</p>
    </section>

    <section v-if="chartPoints.length" class="panel">
      <header class="panel-header"><div><h3>每日请求趋势</h3><span class="sub">外部 Assistant · RAG · 近 7 天；待补充信息不是失败，点击柱形查看当天链路。</span></div></header>
      <TrendChart :points="chartPoints" @select="openBucketDrilldown" />
    </section>

    <section class="panel">
      <header class="panel-header"><div><h3>值得人工复核的问题</h3><span class="sub">以下回答需要用户补充条件，不直接等同于知识库缺口或错误答案。</span></div><el-button text @click="store.navigate('logs', 'tab=assistant-answers')">进入回答复核 →</el-button></header>
      <el-table
        v-if="unconfirmedTraces.length"
        :data="unconfirmedTraces.slice(0, 5)"
        class="clickable-table"
        @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="用户问题"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="结果" width="120"><template #default>待补充条件</template></el-table-column>
        <el-table-column prop="user_id" label="用户" width="140" />
        <el-table-column label="时间" min-width="165"><template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template></el-table-column>
      </el-table>
      <EmptyState
        v-else-if="overview && !metricsError"
        :icon="ChatDotRound"
        title="当前没有待补充信息的问题"
        description="仅展示近 7 天外部 Assistant 的技术回答。仍可进入回答复核查看其他问题。"
      />
      <p v-else class="empty-note">{{ metricsLoading ? '正在加载问题记录…' : '问题记录暂不可确认，请刷新后查看。' }}</p>
    </section>

    <details class="panel advanced-details">
      <summary>更多指标与诊断<span>回答分布、用户明细、请求重放和链路记录</span></summary>
      <div class="advanced-body">
        <section v-if="overview">
          <h4>技术回答结果</h4>
          <p class="muted">外部 Assistant · RAG · 近 7 天；请求 {{ overview.total_traces }} 次，产品范围内已分类 {{ overview.in_scope_traces }} 次。</p>
          <div class="detail-grid">
            <div><span>待补充信息率</span><b>{{ percent(overview.needs_information_rate, overview.in_scope_traces) }}</b><small>{{ overview.response_type_counts.UNCONFIRMED ?? 0 }} / {{ overview.in_scope_traces }} · 范围内已分类回答</small></div>
            <div><span>范围外请求率</span><b>{{ percent(overview.out_of_scope_rate, overview.classified_traces) }}</b><small>{{ overview.response_type_counts.OUT_OF_SCOPE ?? 0 }} / {{ overview.classified_traces }} · 已分类回答</small></div>
            <div><span>技术请求失败率</span><b>{{ percent(overview.failure_rate, overview.total_traces) }}</b><small>{{ overview.failed }} / {{ overview.total_traces }} · 技术请求</small></div>
            <div><span>技术回答 P50 耗时</span><b>{{ latency(overview.latency.p50_ms, overview.latency.samples) }}</b><small>中位数 · 样本 {{ overview.latency.samples }}</small></div>
          </div>
          <h4>回答依据分布</h4>
          <p class="muted">仅统计 {{ overview.answer_basis_traces }} 条带依据分类的回答；依据类型不等于答案正确率。</p>
          <div class="detail-grid">
            <div><span>文档知识</span><b>{{ percent(overview.product_knowledge_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_KNOWLEDGE ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
            <div><span>文档 + 通用解释</span><b>{{ percent(overview.product_explained_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_EXPLAINED ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
            <div><span>通用工程扩展</span><b>{{ percent(overview.product_general_rate, overview.answer_basis_traces) }}</b><small>{{ overview.answer_mode_counts.PRODUCT_GENERAL ?? 0 }} / {{ overview.answer_basis_traces }}</small></div>
          </div>
        </section>
        <section v-if="assistantMetrics">
          <h4>调用与留存诊断</h4>
          <p class="muted">近 {{ assistantMetrics.days }} 天的调用统计；会话留存为当前状态，不是七天新增量。</p>
          <p>幂等重放 {{ assistantMetrics.idempotency.assistant_replays }} 次 / 受保护请求 {{ assistantMetrics.idempotency.assistant_protected }} 次 · 留存会话 {{ assistantMetrics.retention.active }} · 待清理 {{ assistantMetrics.retention.expired }} · 留存 {{ assistantMetrics.retention.retention_days }} 天</p>
          <p v-if="assistantMetrics.turn_error_codes.length">执行错误：{{ assistantMetrics.turn_error_codes.map((item) => item.code + ' × ' + item.count).join('；') }}</p>
          <el-table v-if="assistantMetrics.top_users.length" :data="assistantMetrics.top_users">
            <el-table-column
              prop="user_id"
              label="外部用户"
              min-width="220"
              show-overflow-tooltip
            /><el-table-column prop="turns" label="调用轮次" width="110" /><el-table-column prop="failed" label="失败" width="90" />
          </el-table>
        </section>
        <section>
          <h4>最近链路记录</h4><p class="muted">所选知识库的最近记录，可能包含内部试问与调试台；不属于上方外部运营指标的同一统计口径。</p>
          <el-table
            v-if="recentTraces.length"
            :data="recentTraces"
            class="clickable-table"
            @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
          >
            <el-table-column
              prop="question"
              label="问题"
              min-width="240"
              show-overflow-tooltip
            /><el-table-column prop="source" label="来源" width="160" /><el-table-column label="状态" width="130"><template #default="scope"><StatusBadge :status="scope.row.status" /></template></el-table-column>
          </el-table>
          <p v-else class="muted">暂无记录</p>
        </section>
      </div>
    </details>

    <el-dialog
      :model-value="!!drilldown"
      :title="'当天技术链路 · ' + (drilldown?.label || '')"
      width="min(920px, 94vw)"
      @close="drilldownGeneration++; drilldown = null"
    >
      <p class="muted">外部 Assistant · RAG · {{ drilldown?.from }} 至 {{ drilldown?.to }}，最多展示 100 条。</p>
      <el-alert
        v-if="drilldownError"
        :title="drilldownError"
        type="warning"
        :closable="false"
      />
      <el-table
        v-loading="drilldownLoading"
        :data="drilldownTraces"
        class="clickable-table"
        @row-click="(row: TraceSummary) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="问题"
          min-width="260"
          show-overflow-tooltip
        /><el-table-column prop="user_id" label="用户" width="140" /><el-table-column label="状态" width="130"><template #default="scope"><StatusBadge :status="scope.row.status" /></template></el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.dashboard { display: grid; gap: 20px; min-width: 0; }
.dashboard > * { min-width: 0; }
.dashboard-header .panel-header { border-bottom: none; }
.scope-note { margin: 0; padding: 0 18px 16px; font-size: 12px; color: var(--text-secondary); line-height: 1.6; }
.scope-actions { display: flex; align-items: center; gap: 10px; }
.scope-actions .el-select { width: 220px; }
.status-layout { display: grid; grid-template-columns: 1.1fr 1fr; gap: 20px; }
.availability-panel, .attention-panel { padding: 22px; }
.eyebrow { display: block; color: var(--text-tertiary); font-size: 12px; margin-bottom: 12px; }
.availability-panel h3 { margin: 0 0 8px; font-size: 20px; color: var(--text-primary); }
.availability-panel h3.status-blocked { color: var(--warning); }
.availability-panel p, .attention-panel p { color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.status-meta { display: flex; flex-wrap: wrap; gap: 16px; margin: 16px 0; font-size: 12px; color: var(--text-tertiary); }
.attention-row { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 0; border: 0; border-bottom: 1px solid var(--border-subtle); background: transparent; text-align: left; color: var(--text-primary); cursor: pointer; font-size: 13px; }
.attention-row b { color: var(--brand-primary); font-size: 12px; flex-shrink: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 4px 0 14px; }
.section-heading h3 { margin: 0; font-size: 16px; }
.section-heading p { margin: 6px 0 0; color: var(--text-tertiary); font-size: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.empty-note { padding: 16px 18px; color: var(--text-tertiary); font-size: 13px; }
.advanced-details > summary { padding: 18px; cursor: pointer; color: var(--text-primary); font-size: 14px; }
.advanced-details > summary span { margin-left: 12px; color: var(--text-tertiary); font-size: 12px; }
.advanced-body { display: grid; gap: 24px; padding: 0 18px 18px; }
.advanced-body h4 { margin: 16px 0 8px; }
.advanced-body p { font-size: 12px; line-height: 1.7; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 14px 0; }
.detail-grid > div { display: grid; gap: 6px; padding: 14px; border-radius: var(--radius-sm); background: var(--bg-subtle); }
.detail-grid span, .detail-grid small { color: var(--text-secondary); font-size: 12px; }
.detail-grid b { font-size: 20px; font-variant-numeric: tabular-nums; }
:deep(.clickable-table .el-table__row) { cursor: pointer; }
@media (max-width: 1100px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) {
  .status-layout, .metric-grid { grid-template-columns: 1fr; }
  .dashboard-header .panel-header, .section-heading { align-items: flex-start; flex-direction: column; }
  .scope-actions { width: 100%; flex-wrap: wrap; }
  .scope-actions .el-select { flex: 1; min-width: 150px; }
  .advanced-details > summary span { display: block; margin: 6px 0 0; }
}
</style>
