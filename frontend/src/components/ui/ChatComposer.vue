<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { Promotion } from "@element-plus/icons-vue";

/*
 * Unified chat composer: auto-growing textarea with the send / stop
 * control integrated inside the same surface (no detached button).
 * Enter or Ctrl+Enter sends, Shift+Enter inserts a newline.
 */
const props = defineProps<{
  modelValue: string;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  hint?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  send: [];
  stop: [];
}>();

const textarea = ref<HTMLTextAreaElement | null>(null);

function autoGrow(): void {
  const el = textarea.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

watch(
  () => props.modelValue,
  () => {
    void nextTick(autoGrow);
  },
);

function onInput(event: Event): void {
  emit("update:modelValue", (event.target as HTMLTextAreaElement).value);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== "Enter") return;
  if (event.shiftKey) return;
  event.preventDefault();
  send();
}

function send(): void {
  if (props.disabled || props.loading) return;
  if (!props.modelValue.trim()) return;
  emit("send");
}
</script>

<template>
  <div class="chat-composer" :class="{ disabled }">
    <textarea
      ref="textarea"
      rows="1"
      :value="modelValue"
      :placeholder="placeholder ?? 'Ask a question...'"
      :disabled="disabled"
      @input="onInput"
      @keydown="onKeydown"
    />
    <div class="composer-footer">
      <span class="composer-hint">{{ hint ?? "Enter 发送 · Shift + Enter 换行" }}</span>
      <div class="composer-actions">
        <el-button
          v-if="loading"
          size="small"
          type="danger"
          plain
          @click="emit('stop')"
        >
          停止生成
        </el-button>
        <button
          type="button"
          class="send-button"
          :disabled="disabled || loading || !modelValue.trim()"
          aria-label="发送"
          @click="send"
        >
          <el-icon :size="15"><Promotion /></el-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-composer {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.chat-composer:focus-within {
  border-color: var(--brand-primary);
  box-shadow: 0 0 0 2px var(--brand-subtle);
}

.chat-composer.disabled {
  opacity: 0.6;
}

.chat-composer textarea {
  max-height: 160px;
  padding: 12px 14px 4px;
  border: 0;
  outline: none;
  resize: none;
  color: var(--text-primary);
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
}

.chat-composer textarea::placeholder {
  color: var(--text-tertiary);
}

.composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px 8px 12px;
}

.composer-hint {
  color: var(--text-tertiary);
  font-size: 11px;
  user-select: none;
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.send-button {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  color: #ffffff;
  background: var(--brand-primary);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.send-button:hover:not(:disabled) {
  background: var(--brand-hover);
}

.send-button:disabled {
  cursor: not-allowed;
  background: var(--border-default);
}
</style>
