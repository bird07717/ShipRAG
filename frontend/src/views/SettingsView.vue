<script setup lang="ts">
import { ref } from "vue";

import { useAdminStore } from "@/composables/adminStore";

const store = useAdminStore();

const sections = [
  { key: "api", label: "API 访问" },
  { key: "danger", label: "危险操作" },
];
const activeSection = ref("api");

async function clearCredential(): Promise<void> {
  await store.run(async () => {
    store.clearToken();
  });
}
</script>

<template>
  <div class="settings-view">
    <nav class="settings-nav">
      <button
        v-for="section in sections"
        :key="section.key"
        type="button"
        :class="{ active: activeSection === section.key }"
        @click="activeSection = section.key"
      >
        <span :class="{ 'danger-item': section.key === 'danger' }">{{ section.label }}</span>
      </button>
    </nav>

    <div class="settings-content">
      <section v-show="activeSection === 'api'" class="panel">
        <header class="panel-header">
          <div>
            <h3>API 访问</h3>
            <span class="sub">管理台调用后端服务的访问凭据</span>
          </div>
        </header>
        <div class="panel-body">
          <div class="form-section">
            <h4>Service Token</h4>
            <p class="desc">
              生产环境应由反向代理注入凭据，不在管理台填写。此处输入的凭据仅保存在当前浏览器会话（sessionStorage），关闭标签页后自动失效。
            </p>
            <el-input
              v-model="store.serviceToken"
              type="password"
              show-password
              placeholder="粘贴访问凭据，留空提交即清除"
              style="max-width: 420px"
              @change="store.saveToken(store.serviceToken)"
            />
          </div>
        </div>
      </section>

      <section v-show="activeSection === 'danger'" class="panel danger-zone">
        <header class="panel-header">
          <div>
            <h3>Danger Zone</h3>
            <span class="sub">以下操作立即生效且不可撤销</span>
          </div>
        </header>
        <div class="panel-body">
          <div class="danger-row">
            <div>
              <h4>清除本地访问凭据</h4>
              <p class="desc">
                从当前浏览器会话移除 Service Token，管理台将恢复为匿名访问。
              </p>
            </div>
            <el-button type="danger" plain @click="clearCredential">清除凭据</el-button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: var(--space-content-gap);
  align-items: start;
  max-width: 960px;
}

.settings-nav {
  display: grid;
  gap: 2px;
  padding: 6px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
}

.settings-nav button {
  padding: 8px 12px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition:
    color var(--transition-fast),
    background-color var(--transition-fast);
}

.settings-nav button:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.settings-nav button.active {
  color: var(--brand-primary);
  background: var(--brand-subtle);
  font-weight: 500;
}

.danger-zone {
  border-color: #eecfcd;
}

.danger-zone .panel-header {
  border-bottom-color: #f3dddc;
}

.danger-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.danger-row h4 {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
}

.danger-row .desc {
  max-width: 480px;
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 13px;
}

@media (max-width: 800px) {
  .settings-view {
    grid-template-columns: 1fr;
  }

  .danger-row {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
