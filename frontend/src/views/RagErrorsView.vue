<script setup lang="ts">
import { onMounted, watch } from "vue";

import EngineErrorTable from "@/components/EngineErrorTable.vue";
import { useAdminStore } from "@/composables/adminStore";
import { useRagAdminStore } from "@/composables/ragAdminStore";

const shared = useAdminStore();
const rag = useRagAdminStore();
const load = () => rag.run(() => rag.loadErrors());

watch(() => shared.selectedKnowledgeId, () => void load());
onMounted(() => void load());
</script>

<template>
  <EngineErrorTable
    engine="RAG"
    :traces="rag.errors"
    :loading="rag.loading"
    :error="rag.error"
    @reload="load"
  />
</template>
