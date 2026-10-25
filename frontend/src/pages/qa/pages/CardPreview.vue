<script setup lang="ts">
/**
 * 页面卡片缩略预览：指标卡 / 折线 / 柱状 / 表格；底部标口径（完整名称 + 版本），有待发布变更时提示。
 * 无权限：后台对范围外的卡片返回 {denied:true}（qa-api P3，只留标题 / 图形 / 口径，不出数值），
 * 这里只显示“无权限”（文案同 PageDrawer.vue:128/283），不给数值、不加操作。
 */
import { computed } from 'vue';
import { LockOutlined } from '@ant-design/icons-vue';
import EChart from '@/components/EChart.vue';
import InfoTip from '../InfoTip.vue';
import type { PageCard } from '@/mock/qa-pages';

const props = defineProps<{ card: PageCard & { denied?: boolean }; index?: number }>();
const option = computed(() => ({
  grid: { left: 36, right: 8, top: 8, bottom: 20 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: props.card.labels ?? [], axisTick: { show: false } },
  yAxis: { type: 'value', splitNumber: 2 },
  series: [{ type: props.card.viz === '柱状图' ? 'bar' : 'line', data: props.card.series ?? [], barMaxWidth: 16, symbol: 'none' }],
}));
</script>

<template>
  <div class="cp">
    <div class="cp-head">
      <span v-if="index != null" class="num c-3 t-12">{{ index }}</span>
      <span class="fw-600 cp-ell" :title="card.title">{{ card.title }}</span>
      <InfoTip :title="`口径：${card.metric} ${card.version}${card.pending ? `；${card.pending}` : ''}`" />
      <a-tag v-if="card.pending" class="cp-tag">口径待更新</a-tag>
      <span class="t-12 c-3 cp-viz">{{ card.viz }}</span>
    </div>
    <div class="cp-body">
      <div v-if="card.denied" class="cp-deny t-12 c-3" title="看到的数据按各自的数据权限裁剪，范围外的卡片显示“无权限”。"><LockOutlined /> 无权限</div>
      <template v-else-if="card.viz === '指标卡'">
        <div class="cp-ell" :title="card.sub"><span class="t-20 fw-600 num c-1">{{ card.value }}</span> <span class="t-12 c-3">{{ card.unit }}</span> <span class="t-12 c-2 num">{{ card.sub }}</span></div>
      </template>
      <div v-else-if="card.viz === '表格'" class="cp-tbl">
        <div v-for="r in card.rows" :key="r[0]" class="cp-tr t-12"><span class="cp-ell c-2">{{ r[0] }}</span><span class="num c-1">{{ r[1] }}</span></div>
      </div>
      <div v-else class="cp-chart"><EChart :option="option" /></div>
    </div>
  </div>
</template>

<style scoped>
.cp { border: var(--border); border-radius: var(--radius); padding: 8px 12px; display: flex; flex-direction: column; gap: 4px; min-width: 0; height: 168px; }
.cp-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cp-viz { margin-left: auto; flex: none; }
.cp-tag { margin-inline-end: 0; flex: none; }
.cp-body { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; }
.cp-chart { height: 88px; }
.cp-tbl { display: flex; flex-direction: column; gap: 2px; }
.cp-tr { display: flex; justify-content: space-between; gap: 8px; }
.cp-deny { display: flex; align-items: center; justify-content: center; gap: 4px; }
.cp-ell { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
</style>
