<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import EChart from '@/components/EChart.vue';
import type { QaChainTable } from '@/api/qa';

const props = defineProps<{ table: QaChainTable; question: string; request?: { type: 'bar' | 'line' } }>();
const chartType = (question: string) => /折线图/.test(question) ? 'line' : /柱状图|柱形图|条形图/.test(question) ? 'bar' : 'table';
const view = ref<'table' | 'bar' | 'line'>(chartType(props.question));
watch(() => props.request, (request) => { if (request) view.value = request.type; });
const columns = computed(() => props.table.columns.map((c) => ({ dataIndex: c.key, title: c.title, align: (c.align ?? 'left') as 'left' | 'right' })));
const rows = computed(() => props.table.items.map((row, i) => ({ ...row, _k: i })));
const showTable = computed(() => rows.value.length > 1 || columns.value.length > 1);
const chartData = computed(() => {
  const items = props.table.items;
  if (!items.length) return null;
  const numeric = props.table.columns.filter((col) => items.some((row) => typeof row[col.key] === 'number') && items.every((row) => row[col.key] == null || (typeof row[col.key] === 'number' && Number.isFinite(row[col.key]))));
  const labels = props.table.columns.filter((col) => items.every((row) => typeof row[col.key] === 'string' && String(row[col.key]).trim()));
  if (labels.length !== 1 || !numeric.length || numeric.length + labels.length !== props.table.columns.length) return null;
  const names = items.map((row) => String(row[labels[0].key]));
  if (new Set(names).size !== names.length) return null;
  return { names, numeric };
});
const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { show: (chartData.value?.numeric.length ?? 0) > 1 },
  grid: { left: 60, right: 30, top: 45, bottom: 70, containLabel: true },
  xAxis: { type: 'category', data: chartData.value?.names ?? [], axisLabel: { rotate: (chartData.value?.names.length ?? 0) > 6 ? 30 : 0, hideOverlap: true } },
  yAxis: { type: 'value', name: chartData.value?.numeric.length === 1 ? chartData.value.numeric[0].title : '' },
  series: chartData.value?.numeric.map((col) => ({ name: col.title, type: view.value === 'line' ? 'line' : 'bar', data: props.table.items.map((row) => row[col.key] ?? null), barMaxWidth: 48 })) ?? [],
}));
</script>

<template>
  <div v-if="showTable || view !== 'table'" class="qa-result">
    <div v-if="chartData" class="qa-result-toolbar">
      <a-segmented v-model:value="view" size="small" :options="[{ label: '表格', value: 'table' }, { label: '柱状图', value: 'bar' }, { label: '折线图', value: 'line' }]" />
      <span class="t-12 c-3">{{ table.summary }}</span>
    </div>
    <div v-if="view !== 'table' && chartData" class="qa-result-chart" role="img" :aria-label="`${view === 'bar' ? '柱状图' : '折线图'}：${table.title}`">
      <EChart :option="option" />
    </div>
    <template v-else>
      <div v-if="view !== 'table' && !chartData" class="qa-result-hint">当前结果缺少明确的分类列和数值列，暂不能绘图。</div>
      <a-table v-if="showTable" size="small" row-key="_k" :columns="columns" :data-source="rows" :pagination="table.paged ? { pageSize: 10, size: 'small', showSizeChanger: false } : false" :scroll="{ x: 'max-content' }" />
    </template>
  </div>
</template>

<style scoped>
.qa-result { margin: 0 40px 10px 0; background: var(--g1); }
.qa-result-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border: var(--border); border-bottom: 0; }
.qa-result-chart { height: 320px; border: var(--border); }
.qa-result-hint { padding: 10px 12px; color: var(--g8); }
</style>
