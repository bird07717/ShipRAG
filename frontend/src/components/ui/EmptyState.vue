<script setup lang="ts">
import type { Component } from "vue";

defineProps<{
  icon?: Component;
  title: string;
  description?: string;
}>();
</script>

<template>
  <div class="empty-state">
    <div class="empty-icon">
      <el-icon v-if="icon" :size="18"><component :is="icon" /></el-icon>
      <span v-else>◎</span>
    </div>
    <h4>{{ title }}</h4>
    <p v-if="description">{{ description }}</p>
    <div class="empty-action">
      <slot name="action" />
    </div>
  </div>
</template>
