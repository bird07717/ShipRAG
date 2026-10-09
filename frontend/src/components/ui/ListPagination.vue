<script setup lang="ts">
import { computed } from "vue";

import { PAGE_SIZES } from "@/utils/paging";

/*
 * Shared pager for server-paged admin lists. It stays visible while there is
 * more than one minimal page or the user is not on page 1, so a list that
 * shrank under the user always keeps a way back.
 */
const props = defineProps<{ total: number }>();
const page = defineModel<number>("page", { required: true });
const pageSize = defineModel<number>("pageSize", { required: true });
const emit = defineEmits<{
  /** Page changed: reload with the same filters. */
  "page-change": [page: number];
  /** Page size changed: reload from the first page. */
  "size-change": [size: number];
}>();

const visible = computed(() => props.total > PAGE_SIZES[0]! || page.value > 1);
</script>

<template>
  <el-pagination
    v-if="visible"
    v-model:current-page="page"
    v-model:page-size="pageSize"
    class="list-pagination"
    :total="total"
    :page-sizes="PAGE_SIZES"
    layout="total, sizes, prev, pager, next"
    size="small"
    background
    @current-change="(value: number) => emit('page-change', value)"
    @size-change="(value: number) => emit('size-change', value)"
  />
</template>
