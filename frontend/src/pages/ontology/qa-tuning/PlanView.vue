<script setup lang="ts">
/** 语义计划（不含 SQL）：指标、维度、时间、过滤、排序；由本体查询模块翻译成各目标库查询 */
import type { SemPlan } from '@/mock/qa-tuning';
import MetricRef from './MetricRef.vue';

defineProps<{ plan: SemPlan }>();
</script>

<template>
  <div class="pv">
    <span class="t-12 c-3">指标</span><span class="t-14"><MetricRef :k="plan.metric" /></span>
    <span class="t-12 c-3">维度</span><span class="t-14 c-1">{{ plan.dims.length ? plan.dims.join('、') : '不拆分（汇总）' }}</span>
    <span class="t-12 c-3">时间</span><span class="t-14 c-1">{{ plan.time }}</span>
    <span class="t-12 c-3">过滤</span><span class="t-14 c-1">{{ plan.filter ?? '--' }}</span>
    <span class="t-12 c-3">排序</span><span class="t-14 c-1">{{ plan.sort ?? '--' }}</span>
  </div>
</template>

<style scoped>
.pv { display: grid; grid-template-columns: 48px 1fr; gap: 6px 12px; align-items: baseline; }
</style>
