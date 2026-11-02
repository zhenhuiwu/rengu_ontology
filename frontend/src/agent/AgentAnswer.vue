<script setup lang="ts">
import { computed, ref } from 'vue';
import { use as register } from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
import VChart from 'vue-echarts';
import type { PublicTurn } from './api';

register([SVGRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent]);
const props = defineProps<{ turn: PublicTurn; busy: boolean; navy: boolean }>();
const emit = defineEmits<{ (e: 'clarify', question: string): void }>();
const text = computed(() => props.turn.answer?.text ?? props.turn.text ?? [props.turn.lead, props.turn.bold, props.turn.tail].filter(Boolean).join(''));
const table = computed(() => props.turn.answer?.table);
const chartType = (question: string) => /折线图/.test(question) ? 'line' : /柱状图|柱形图|条形图/.test(question) ? 'bar' : 'table';
const view = ref<'table' | 'bar' | 'line'>(chartType(props.turn.question ?? ''));
const showTable = computed(() => {
  const t = table.value, answerText = props.turn.answer?.text;
  return !(t && t.items.length === 1 && t.total === 1 && t.columns.length === 1 && answerText && t.items[0][t.columns[0].key] === answerText);
});
const columns = computed(() => table.value?.columns.map(c => ({ title: c.title, dataIndex: c.key, key: c.key, align: c.align === 'right' ? 'right' as const : 'left' as const })) ?? []);
const rows = computed(() => table.value?.items.map((row, index) => ({ ...row, __agentRow: index })) ?? []);
const chartData = computed(() => {
  const t = table.value;
  if (!t?.items.length) return null;
  const numeric = t.columns.filter(c => t.items.some(r => typeof r[c.key] === 'number') && t.items.every(r => r[c.key] == null || typeof r[c.key] === 'number' && Number.isFinite(r[c.key])));
  const labels = t.columns.filter(c => t.items.every(r => typeof r[c.key] === 'string' && String(r[c.key]).trim()));
  if (labels.length !== 1 || !numeric.length || numeric.length + labels.length !== t.columns.length) return null;
  const names = t.items.map(r => String(r[labels[0].key]));
  return new Set(names).size === names.length ? { names, numeric } : null;
});
const chartOption = computed(() => ({
  color: ['#3B82F6', '#7aa3e5', '#8EA0C0'],
  textStyle: { color: props.navy ? '#D5DEEE' : '#595959', fontFamily: 'sans-serif', fontSize: 12 },
  tooltip: { trigger: 'axis', renderMode: 'richText' },
  legend: { show: (chartData.value?.numeric.length ?? 0) > 1, textStyle: { color: props.navy ? '#D5DEEE' : '#595959' } },
  grid: { left: 12, right: 12, top: 40, bottom: 12, containLabel: true },
  xAxis: { type: 'category', data: chartData.value?.names ?? [], axisLabel: { hideOverlap: true, color: props.navy ? '#B7C4DA' : '#595959' } },
  yAxis: { type: 'value', axisLabel: { color: props.navy ? '#B7C4DA' : '#595959' }, splitLine: { lineStyle: { color: props.navy ? '#3A4C70' : '#e5e6e8' } } },
  series: chartData.value?.numeric.map(c => ({ name: c.title, type: view.value === 'line' ? 'line' : 'bar', barMaxWidth: 40, data: table.value?.items.map(r => r[c.key] ?? null) })) ?? [],
}));
function clarify(label: string) { emit('clarify', label); }
function cell(value: unknown) { return value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
</script>

<template>
  <div class="agent-answer">
    <div v-if="text" class="agent-answer-text">{{ text }}</div>
    <div v-if="turn.answer?.card === 'clarify'" class="agent-options">
      <a-button v-for="(option, index) in turn.answer.clarify?.options ?? []" :key="index" :disabled="busy" @click="clarify(option.label)">{{ option.label }}</a-button>
    </div>
    <section v-if="table && showTable" class="agent-result" :aria-label="table.title || '查询结果'">
      <div class="agent-result-head">
        <span class="fw-600">{{ table.title || '查询结果' }}</span>
        <a-segmented v-if="chartData" v-model:value="view" size="small" :options="[{ label: '表格', value: 'table' }, { label: '柱状图', value: 'bar' }, { label: '折线图', value: 'line' }]" />
      </div>
      <a-table v-if="view === 'table' || !chartData" size="small" row-key="__agentRow" :columns="columns" :data-source="rows" :pagination="rows.length > 10 ? { pageSize: 10, size: 'small', showSizeChanger: false } : false" :scroll="{ x: 'max-content' }">
        <template #bodyCell="{ text: value }">{{ cell(value) }}</template>
        <template #emptyText>没有符合条件的数据</template>
      </a-table>
      <div v-else class="agent-chart" role="img" :aria-label="`${view === 'bar' ? '柱状图' : '折线图'}：${table.title}`"><VChart :option="chartOption" autoresize /></div>
      <div v-if="table.summary" class="agent-result-summary t-12 c-3">{{ table.summary }}</div>
    </section>
  </div>
</template>

<style scoped>
.agent-answer { min-width: 0; }
.agent-answer-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.agent-options { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.agent-options :deep(.ant-btn) { height: auto; min-height: 36px; white-space: normal; text-align: left; }
.agent-result { min-width: 0; margin-top: 16px; border: var(--border); border-radius: var(--radius); overflow: hidden; }
.agent-result-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; padding: 12px; }
.agent-result :deep(.ant-table-content > table) { min-width: 100%; }
.agent-result :deep(.ant-table-cell) { max-width: 360px; overflow-wrap: anywhere; }
.agent-result :deep(.ant-pagination) { margin-inline: 12px; }
.agent-result-summary { padding: 10px 12px; border-top: var(--border); }
.agent-chart { height: 280px; padding: 0 8px; }
</style>
