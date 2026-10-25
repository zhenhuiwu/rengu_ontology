<script setup lang="ts">
/**
 * 草稿横幅：“草稿 CR-0012 · 未发布”。放在建模类页面主体最上面：<DraftBanner />
 * 没有加入草稿、或正在看历史版本时不显示（不占高度）。高度 var(--banner-h)。
 */
import { useAppStore } from '@/stores/app';
const app = useAppStore();
async function switchDraft() { await app.askJoinDraft(); }
</script>

<template>
  <div v-if="app.activeDraft && app.viewMode === 'current'" class="db t-12">
    <span class="db-tag fw-600">草稿 {{ app.activeDraft }} · 未发布</span>
    <span v-if="app.draft" class="c-2">{{ app.draft.title }} · {{ app.draft.changes }} 处修改 · 最近保存 {{ app.draft.updatedAt }}</span>
    <span class="db-sp" />
    <a @click="switchDraft">切换草稿</a>
    <a class="c-2" @click="app.setDraft(null)">退出草稿</a>
  </div>
</template>

<style scoped>
.db { height: var(--banner-h); flex: none; display: flex; align-items: center; gap: 12px; padding: 0 12px; margin-bottom: var(--gap); background: var(--brand-bg); border-radius: var(--radius); }
.db-tag { color: var(--brand); }
.db-sp { flex: 1; }
</style>
