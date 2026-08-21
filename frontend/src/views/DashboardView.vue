<script setup lang="ts">
import { Box, ChatDotRound, Clock, Files, Notebook } from "@element-plus/icons-vue";

import { useAdminStore } from "@/composables/adminStore";
import MetricCard from "@/components/ui/MetricCard.vue";
import StatusBadge from "@/components/ui/StatusBadge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";

const store = useAdminStore();
</script>

<template>
  <div class="dashboard">
    <div class="metric-grid">
      <MetricCard
        label="Knowledge Bases"
        :value="store.knowledgeBases.length"
        context="独立产品域"
        :icon="Notebook"
      />
      <MetricCard
        label="Documents"
        :value="store.totalDocuments.toLocaleString()"
        context="有效 Word 源"
        :icon="Files"
      />
      <MetricCard
        label="Indexed Chunks"
        :value="store.totalChunks.toLocaleString()"
        context="当前在线快照"
        :icon="Box"
      />
      <MetricCard
        label="Recent Queries"
        :value="store.traces.length"
        context="最近 50 条 Trace"
        :icon="ChatDotRound"
      />
    </div>

    <section class="panel">
      <header class="panel-header">
        <div>
          <h3>Recent Activity</h3>
          <span class="sub">最近的 Playground 与 Chat 查询</span>
        </div>
      </header>
      <el-table
        v-if="store.traces.length"
        :data="store.traces.slice(0, 10)"
        @row-click="(row: any) => store.openTrace(row.trace_id)"
      >
        <el-table-column
          prop="question"
          label="Query"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column prop="mode" label="Mode" width="120" />
        <el-table-column label="Status" width="130">
          <template #default="scope"><StatusBadge :status="scope.row.status" /></template>
        </el-table-column>
        <el-table-column label="Latency" width="110">
          <template #default="scope">
            <span class="mono">{{ scope.row.latency.total_ms ?? "—" }} ms</span>
          </template>
        </el-table-column>
        <el-table-column label="Time" min-width="170">
          <template #default="scope">{{ store.formatDate(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else
        :icon="Clock"
        title="暂无最近查询"
        description="在 RAG Playground 或 Demo Chat 中发起查询后，这里会展示最近的活动。"
      />
    </section>
  </div>
</template>

<style scoped>
.dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-content-gap);
  min-width: 0;
}

.dashboard > * {
  min-width: 0;
}
</style>
