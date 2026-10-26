<script setup lang="ts">
import { ArrowDown } from "@element-plus/icons-vue";
import { reactive, type Component } from "vue";

export interface NavigationItem {
  key: string;
  label: string;
  subtitle: string;
  icon: Component;
}

export interface NavigationGroup {
  label: string;
  items: NavigationItem[];
  /** Collapsed by default unless it contains the active view (legacy areas). */
  collapsible?: boolean;
}

const props = withDefaults(
  defineProps<{
    groups: NavigationGroup[];
    active: string;
    collapsed?: boolean;
    badges?: Record<string, number>;
  }>(),
  { collapsed: false, badges: () => ({}) },
);
const emit = defineEmits<{ navigate: [view: string] }>();

const expanded = reactive<Record<string, boolean>>({});

function isOpen(group: NavigationGroup): boolean {
  if (!group.collapsible || props.collapsed) return true;
  return expanded[group.label] ?? group.items.some((item) => item.key === props.active);
}

function toggle(group: NavigationGroup): void {
  if (group.collapsible) expanded[group.label] = !isOpen(group);
}
</script>

<template>
  <nav class="app-nav" aria-label="主导航">
    <div
      v-for="group in groups"
      :key="group.label"
      class="nav-group"
      :class="{ 'is-collapsible': group.collapsible }"
    >
      <slot name="before-group" :group="group" />
      <div
        v-if="group.collapsible && !collapsed"
        class="nav-group-label nav-group-toggle"
        role="button"
        tabindex="0"
        :aria-expanded="isOpen(group)"
        @click="toggle(group)"
        @keydown.enter.prevent="toggle(group)"
        @keydown.space.prevent="toggle(group)"
      >
        <span>{{ group.label }}</span>
        <el-icon class="toggle-icon" :class="{ open: isOpen(group) }"><ArrowDown /></el-icon>
      </div>
      <div v-else class="nav-group-label"><span>{{ group.label }}</span></div>
      <div v-show="isOpen(group)" class="nav-items">
        <el-tooltip
          v-for="item in group.items"
          :key="item.key"
          :content="item.label"
          placement="right"
          :disabled="!collapsed"
          :show-after="150"
        >
          <button
            type="button"
            :class="{ active: active === item.key }"
            :aria-current="active === item.key ? 'page' : undefined"
            :aria-label="collapsed ? item.label : undefined"
            :title="collapsed ? undefined : item.subtitle"
            @click="emit('navigate', item.key)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span class="nav-label">{{ item.label }}</span>
            <span v-if="badges[item.key]" class="nav-badge" :aria-label="`${badges[item.key]} 项进行中`">{{ badges[item.key] }}</span>
          </button>
        </el-tooltip>
      </div>
    </div>
  </nav>
</template>
