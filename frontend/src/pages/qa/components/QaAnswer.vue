<script setup lang="ts">
/** 主回答卡：一行灰色元信息 + 可展开“依据”；澄清；结论；指标 + 趋势；省份（表格/图表切换）；操作 */
import { ref, computed } from 'vue';
import { ArrowDownOutlined, SearchOutlined, ThunderboltOutlined, PushpinOutlined,
  LikeOutlined, DislikeOutlined, CopyOutlined, DownOutlined, UpOutlined, ApartmentOutlined } from '@ant-design/icons-vue';
import EChart from '@/components/EChart.vue';
import { GRAY } from '@/theme';
import { QA_PROVINCES, QA_TREND, QA_TOTAL_SALES, QA_TOTAL_CUSTOMERS, fmtMoney, fmtInt } from '@/mock';

const emit = defineEmits<{ (e: 'suggest'): void; (e: 'pin'): void }>();
const caliber = ref('ex');
const showBasis = ref(false);
const view = ref('表格');
const f1 = (n: number) => fmtMoney(n, 1);
const columns = [
  { title: '省份', dataIndex: 'province', key: 'province', width: '16%' },
  { title: '复购客户销售额（万元）', dataIndex: 'sales', key: 'sales', align: 'right' as const },
  { title: '环比', dataIndex: 'mom', key: 'mom', align: 'right' as const },
  { title: '复购客户（人）', dataIndex: 'customers', key: 'customers', align: 'right' as const },
  { title: '占比', key: 'share', align: 'right' as const },
];
const axis = { axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12 } };
const valueAxis = { type: 'value', min: 0, splitNumber: 4, axisLabel: { color: GRAY.g8, fontSize: 12 }, splitLine: { lineStyle: { color: GRAY.g4 } } };
const label = { show: true, fontSize: 12, color: GRAY.g9, formatter: (p: { value: number }) => f1(p.value) };
const trendOption = {
  grid: { left: 44, right: 44, top: 20, bottom: 22 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: QA_TREND.months, ...axis },
  yAxis: valueAxis,
  series: [{ type: 'line', symbol: 'circle', symbolSize: 5, lineStyle: { width: 2 }, label,
    data: QA_TREND.values.map((v, i) => (i === QA_TREND.values.length - 1 ? { value: v, label: { position: 'right' } } : v)) }],
};
const avg = QA_TOTAL_SALES / QA_PROVINCES.length;
const barOption = computed(() => ({
  grid: { left: 44, right: 16, top: 24, bottom: 22 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: QA_PROVINCES.map((p) => p.province), ...axis },
  yAxis: valueAxis,
  series: [{ type: 'bar', barMaxWidth: 28, data: QA_PROVINCES.map((p) => p.sales), label: { ...label, position: 'top' },
    markLine: { symbol: 'none', silent: true, lineStyle: { color: GRAY.g7, type: 'dashed' },
      label: { position: 'insideEndTop', fontSize: 12, color: GRAY.g8, formatter: `省均 ${f1(avg)}` }, data: [{ yAxis: avg }] } }],
}));
</script>

<template>
  <div class="qa-ans">
    <div class="qa-ans-head">
      <a-avatar size="small" shape="square" class="qa-ans-av"><template #icon><ApartmentOutlined /></template></a-avatar>
      <span class="fw-600">智能问数</span>
      <span class="c-3">复购客户销售额（不含税）v3 · 数据截至 2026-10-24 23:00 ·
        <a-tooltip title="本体版本、筛选、口径校验与权限过滤">
          <a class="qa-ans-basis-link" @click="showBasis = !showBasis">依据 <UpOutlined v-if="showBasis" /><DownOutlined v-else /></a>
        </a-tooltip>
      </span>
    </div>

    <a-descriptions v-if="showBasis" bordered size="small" :column="2" class="qa-ans-basis" :label-style="{ width: '96px' }">
      <a-descriptions-item label="本体版本">2026.10.25-1</a-descriptions-item>
      <a-descriptions-item label="指标">复购客户销售额（不含税）v3</a-descriptions-item>
      <a-descriptions-item label="筛选">大区 = 华东、客户分群 = 复购客户</a-descriptions-item>
      <a-descriptions-item label="维度">省份</a-descriptions-item>
      <a-descriptions-item label="口径">已出库、剔除退款；口径校验通过</a-descriptions-item>
      <a-descriptions-item label="权限">已按行级权限过滤</a-descriptions-item>
    </a-descriptions>

    <a-alert type="info" show-icon class="qa-ans-clarify">
      <template #message>
        <div class="qa-ans-clarify-row">
          <span>“销售额”有两种口径，已按默认口径 <span class="fw-600">不含税</span> 回答</span>
          <a-radio-group v-model:value="caliber" size="small" option-type="button"
            :options="[{ label: '不含税', value: 'ex' }, { label: '含税', value: 'in' }, { label: '两者对比', value: 'both' }]" />
        </div>
      </template>
    </a-alert>

    <p class="qa-ans-text">
      2026 年 9 月，华东大区复购客户销售额（不含税）为 <span class="fw-600 num">1,286.0 万元</span>，环比下降 8.4%（8 月 1,404.0 万元），占大区销售额 62.0%。7 个省份全部下降，安徽（-12.5%）和江苏（-9.8%）降幅最大。
    </p>

    <div class="qa-ans-row">
      <div class="qa-ans-left">
        <a-descriptions bordered size="small" :column="2" :label-style="{ width: '84px', whiteSpace: 'nowrap' }">
          <a-descriptions-item label="销售额"><span class="fw-600 num">¥1,286.0 万</span></a-descriptions-item>
          <a-descriptions-item label="环比"><span class="fw-600 num c-error"><ArrowDownOutlined /> 8.4%</span></a-descriptions-item>
          <a-descriptions-item label="复购客户"><span class="fw-600 num">{{ fmtInt(QA_TOTAL_CUSTOMERS) }} 人</span></a-descriptions-item>
          <a-descriptions-item label="占大区"><span class="fw-600 num">62.0%</span></a-descriptions-item>
        </a-descriptions>
        <div class="qa-ans-box qa-ans-trend">
          <div class="qa-ans-box-head"><span>近 6 个月趋势</span><span class="t-12 c-3">万元</span></div>
          <div class="qa-ans-chart"><EChart :option="trendOption" /></div>
        </div>
      </div>
      <div class="qa-ans-box qa-ans-prov">
        <div class="qa-ans-box-head"><span>按省份拆分</span>
          <a-segmented v-model:value="view" size="small" :options="['表格', '图表']" /></div>
        <a-table v-if="view === '表格'" size="small" :columns="columns" :data-source="QA_PROVINCES" row-key="key" :pagination="false" class="qa-ans-tbl">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'sales'" class="num">{{ f1(record.sales) }}</span>
            <span v-else-if="column.key === 'mom'" class="num">{{ f1(record.mom) }}%</span>
            <span v-else-if="column.key === 'customers'" class="num">{{ fmtInt(record.customers) }}</span>
            <span v-else-if="column.key === 'share'" class="num">{{ f1((record.sales / QA_TOTAL_SALES) * 100) }}%</span>
          </template>
          <template #summary>
            <a-table-summary-row class="qa-ans-sum">
              <a-table-summary-cell :index="0"><span class="fw-600">7 省合计</span></a-table-summary-cell>
              <a-table-summary-cell :index="1" align="right"><span class="fw-600 num">{{ f1(QA_TOTAL_SALES) }}</span></a-table-summary-cell>
              <a-table-summary-cell :index="2" align="right"><span class="num">-8.4%</span></a-table-summary-cell>
              <a-table-summary-cell :index="3" align="right"><span class="fw-600 num">{{ fmtInt(QA_TOTAL_CUSTOMERS) }}</span></a-table-summary-cell>
              <a-table-summary-cell :index="4" align="right"><span class="num">100.0%</span></a-table-summary-cell>
            </a-table-summary-row>
          </template>
        </a-table>
        <div v-else class="qa-ans-chart"><EChart :option="barOption" /></div>
      </div>
    </div>

    <div class="qa-ans-actions">
      <a-space :size="8">
        <a-button><template #icon><SearchOutlined /></template>查看原因</a-button>
        <a-button type="primary" @click="emit('suggest')"><template #icon><ThunderboltOutlined /></template>建议动作</a-button>
        <a-button @click="emit('pin')"><template #icon><PushpinOutlined /></template>固定到页面</a-button>
      </a-space>
      <a-space :size="4">
        <a-tooltip title="复制"><a-button type="text"><template #icon><CopyOutlined /></template></a-button></a-tooltip>
        <a-tooltip title="回答正确"><a-button type="text"><template #icon><LikeOutlined /></template></a-button></a-tooltip>
        <a-tooltip title="回答有误"><a-button type="text"><template #icon><DislikeOutlined /></template></a-button></a-tooltip>
      </a-space>
    </div>
  </div>
</template>

<style scoped>
.qa-ans { background: var(--g1); border: var(--border); border-radius: var(--radius); padding: 12px 16px; margin-right: 40px; }
.qa-ans-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.qa-ans-av { background: var(--g1); border: 1px solid var(--g6); color: var(--brand); }
.qa-ans-basis-link { color: var(--g8); }
.qa-ans-basis-link:hover { color: var(--brand); }
.qa-ans-basis { margin-bottom: 10px; }
.qa-ans-clarify { padding: 4px 12px; margin-bottom: 10px; }
.qa-ans-clarify-row { display: flex; align-items: center; gap: 12px; }
.qa-ans-text { margin: 0 0 10px; }
.qa-ans-row { display: flex; gap: 12px; margin-bottom: 12px; }
.qa-ans-left { flex: 0 0 40%; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.qa-ans-box { border: var(--border); border-radius: var(--radius); padding: 8px 12px 4px; display: flex; flex-direction: column; min-width: 0; }
.qa-ans-box-head { display: flex; justify-content: space-between; align-items: center; height: 24px; margin-bottom: 4px; }
.qa-ans-trend { flex: 1; min-height: 0; }
.qa-ans-chart { flex: 1; min-height: 0; }
.qa-ans-prov { flex: 1; padding-bottom: 8px; }
.qa-ans-tbl :deep(th) { white-space: nowrap; }
.qa-ans-tbl :deep(.ant-table-small .ant-table-thead > tr > th),
.qa-ans-tbl :deep(.ant-table-small .ant-table-tbody > tr > td),
.qa-ans-tbl :deep(.ant-table-small .ant-table-summary td) { padding-top: 6px; padding-bottom: 6px; }
.qa-ans-sum :deep(td) { background: var(--g2); }
.qa-ans-actions { display: flex; justify-content: space-between; align-items: center; border-top: var(--border); padding-top: 10px; }
</style>
