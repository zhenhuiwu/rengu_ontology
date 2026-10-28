<script setup lang="ts">
/** 草稿视图顶部常驻提示条，高度用 --banner-h。草稿按规范用灰色（中性底 + 正文色），不用橙色。 */
import type { DraftCR } from '@/mock/releases';
import { useAppStore } from '@/stores/app';
import { useRoute, useRouter } from 'vue-router';
defineProps<{ draft: DraftCR }>();
defineEmits<{ submit: []; discard: [] }>();
const app = useAppStore();
const route = useRoute();
const router = useRouter();
const back = () => router.push(String(route.query.back));
</script>

<template>
  <div class="db t-12">
    <span class="db-tag">草稿 · 未发布</span>
    <span class="c-2 num">变更单 {{ draft.id }}　{{ draft.title }}　{{ draft.changes }} 处改动　{{ draft.owner }} 最后编辑于 {{ draft.updatedAt }}</span>
    <span class="c-3">问数仍使用已发布版本 {{ app.ontologyVersion }}</span>
    <span class="db-sp" />
    <a v-if="route.query.back" @click="back">返回变更单 {{ draft.id }}</a>
    <router-link v-if="app.workspace?.directPublishEnabled" :to="{ path: '/build/ontology/releases', query: { ...route.query, cr: draft.id } }">前往直接发布</router-link>
    <span v-else class="c-3">无直接发布权限</span>
    <a class="c-3" @click="$emit('discard')">放弃草稿</a>
  </div>
</template>

<style scoped>
.db { display: flex; align-items: center; gap: 12px; height: var(--banner-h); padding: 0 16px; background: var(--g3); border-bottom: var(--border); flex: none; }
.db-tag { color: var(--g10); font-weight: var(--fw-strong); }
.db-sp { flex: 1; }
</style>
