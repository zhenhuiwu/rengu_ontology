<script setup lang="ts">
/** 历史版本横幅（AppLayout 自动显示，页面里不用写）。高度 var(--banner-h)。 */
import { HistoryOutlined } from '@ant-design/icons-vue';
import { useAppStore } from '@/stores/app';
import { useVersion } from '@/composables/useVersion';
const app = useAppStore();
const { exit } = useVersion();
</script>

<template>
  <div v-if="app.viewMode === 'history'" class="vb t-12" role="status">
    <HistoryOutlined class="c-warning" />
    <span class="c-1">正在查看历史版本 <span class="num">{{ app.viewVersion }}</span> · 只读 ·</span>
    <a class="vb-back" @click="exit">返回当前版本</a>
  </div>
</template>

<style scoped>
.vb { height: var(--banner-h); flex: none; display: flex; align-items: center; justify-content: center; gap: 6px; background: var(--warning-bg); border-bottom: var(--border); }
.vb-back { color: var(--brand); }
</style>
