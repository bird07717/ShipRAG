<script setup lang="ts">
import { onMounted, watch } from "vue";

import EngineErrorTable from "@/components/EngineErrorTable.vue";
import { useAdminStore } from "@/composables/adminStore";
import { useSupportAdminStore } from "@/composables/supportAdminStore";

const shared = useAdminStore();
const support = useSupportAdminStore();
const load = () => support.run(() => support.loadErrors());

watch(() => shared.selectedKnowledgeId, () => void load());
onMounted(() => void load());
</script>

<template>
  <EngineErrorTable
    engine="SUPPORT"
    :traces="support.errors"
    :loading="support.loading"
    :error="support.error"
    @reload="load"
  />
</template>

