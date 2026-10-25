<script setup lang="ts">
/** 占位页：二级页面还没设计时显示。设计好后在 src/nav.ts 给该页面加 component 即可替换。 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { findPage } from '@/nav';

const route = useRoute();
const page = computed(() => findPage(route.meta.navKey as string));
</script>

<template>
  <div class="panel ph-wrap">
    <div class="ph-box">
      <div class="t-16 fw-600 c-1">{{ page?.title }}</div>
      <div class="t-14 c-3">待设计</div>
      <a-descriptions v-if="page" :column="1" size="small" bordered class="ph-desc" :label-style="{ width: '96px' }">
        <a-descriptions-item label="所属">{{ page.area.title }} / {{ page.module.title }}{{ page.group ? ' / ' + page.group : '' }}</a-descriptions-item>
        <a-descriptions-item label="排期">第{{ ['一', '二', '三', '四'][page.phase - 1] }}期</a-descriptions-item>
        <a-descriptions-item label="路由"><span class="num">{{ page.path }}</span></a-descriptions-item>
        <a-descriptions-item v-if="page.note" label="v5 说明">{{ page.note }}</a-descriptions-item>
      </a-descriptions>
    </div>
  </div>
</template>

<style scoped>
.ph-wrap { flex: 1; align-items: center; justify-content: center; }
.ph-box { width: 560px; display: flex; flex-direction: column; gap: 4px; }
.ph-desc { margin-top: 16px; }
</style>
