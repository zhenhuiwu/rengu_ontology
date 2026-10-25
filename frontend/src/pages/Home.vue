<script setup lang="ts">
/**
 * 首页（示例页）：演示公共组件的用法——KPI 条（.t-kpi）、分页表格、ObjRef（悬停卡 + 抽屉）、
 * ConfirmActionModal、EChart。整页无滚动，表格分页。
 */
import { computed, ref } from 'vue';
import { message } from 'ant-design-vue';
import ObjRef from '@/components/ObjRef.vue';
import HoverCard from '@/components/HoverCard.vue';
import EChart from '@/components/EChart.vue';
import AppPagination from '@/components/AppPagination.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useConfirmStore } from '@/stores/confirm';
import { GRAY } from '@/theme';
import {
  ORDERS, STORES, METRICS, DAILY_GMV_EX, storeById, paginate, fmtMoney, fmtInt, fmtPct, fmtDelta, type Order,
} from '@/mock';

const confirm = useConfirmStore();

const kpis = ['gmv', 'order_cnt', 'gross_margin', 'ship_ontime'].map((id) => {
  const m = METRICS.find((x) => x.id === id)!;
  const value = m.unit === '%' ? fmtPct(m.value) : m.unit === '元' ? fmtMoney(m.value) : fmtInt(m.value);
  const delta = m.unit === '%' ? m.value - m.prev : (m.value - m.prev) / m.prev;
  const deltaText = m.unit === '%' ? `${delta > 0 ? '+' : ''}${(delta * 100).toFixed(2)} pp` : fmtDelta(delta);
  return { m, value, unit: m.unit === '%' ? '' : m.unit, delta, deltaText };
});

// 订单表
const status = ref<string>('全部');
const channel = ref<string>('全部');
const kw = ref('');
// 分页：一屏 15 行（1920×1080），页码/条数同步到 URL，筛选变化回到第 1 页
const pq = usePagedQuery({ defaultSize: 15, filters: [status, channel, kw] });
const rows = computed(() =>
  ORDERS.filter((o) =>
    (status.value === '全部' || o.status === status.value) &&
    (channel.value === '全部' || o.channel === channel.value) &&
    (!kw.value || o.id.includes(kw.value) || (storeById(o.storeId)?.name ?? '').includes(kw.value)),
  ),
);
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const statusBadge = (s: Order['status']) =>
  s === '已出库' || s === '已签收' ? 'success' : s === '已取消' ? 'default' : s === '待支付' ? 'warning' : 'processing';
const columns = [
  { title: '订单号', dataIndex: 'id', key: 'id', width: 190 },
  { title: '门店', key: 'store', ellipsis: true },
  { title: '渠道', dataIndex: 'channel', key: 'channel', width: 112 },
  { title: '商品数', key: 'lines', width: 76, align: 'right' as const },
  { title: '实付金额（元）', key: 'amount', width: 132, align: 'right' as const },
  { title: '状态', key: 'status', width: 96 },
  { title: '下单时间', dataIndex: 'createdAt', key: 'createdAt', width: 172 },
  { title: '操作', key: 'op', width: 92 },
];

async function resend(o: Order) {
  const s = storeById(o.storeId);
  const ok = await confirm.ask({
    title: '补发出库单',
    target: `订单 ${o.id}`,
    fields: [
      { label: '门店', value: s?.name ?? '--' },
      { label: '发货仓', value: o.warehouse },
      { label: '实付金额', value: `${fmtMoney(o.amount)} 元` },
    ],
    impact: '写回 WMS，生成新的出库单；原出库单保留，状态不变。',
    irreversible: true,
  });
  if (ok) message.success(`已提交补发：${o.id}`);
}

// 趋势图
const trendOption = {
  grid: { left: 56, right: 16, top: 16, bottom: 28 },
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
  xAxis: {
    type: 'category', data: DAILY_GMV_EX.map((d) => d.date),
    axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false },
    axisLabel: { color: GRAY.g8, fontSize: 12, interval: 4 },
  },
  yAxis: {
    type: 'value', axisLabel: { color: GRAY.g8, fontSize: 12, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` },
    splitLine: { lineStyle: { color: GRAY.g4 } },
  },
  series: [{ type: 'line', data: DAILY_GMV_EX.map((d) => d.value), symbol: 'none', lineStyle: { width: 1.5 } }],
};

// 门店排行（按样本订单实付金额汇总）
const ranking = computed(() =>
  STORES.map((s) => {
    const os = ORDERS.filter((o) => o.storeId === s.id);
    return { id: s.id, name: s.name, cnt: os.length, amount: os.reduce((a, o) => a + o.amount, 0) };
  }).sort((a, b) => b.amount - a.amount).slice(0, 8),
);
</script>

<template>
  <Teleport defer to="#page-extra">
    <span class="t-12 c-3">数据更新于 2026-10-25 06:00:12</span>
    <a-button>导出</a-button>
  </Teleport>

  <div class="home">
    <div class="panel kpis">
      <div v-for="k in kpis" :key="k.m.id" class="kpi">
        <HoverCard kind="metric" :id="k.m.id"><span class="t-14 c-2">{{ k.m.name }}</span></HoverCard>
        <div class="kpi-val">
          <span class="t-kpi fw-600 c-1">{{ k.value }}</span>
          <span v-if="k.unit" class="t-14 c-2">{{ k.unit }}</span>
        </div>
        <div class="t-12 c-3">
          较上月同期 <span class="num" :class="k.delta >= 0 ? 'c-success' : 'c-error'">{{ k.deltaText }}</span>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="panel orders">
        <div class="panel-head">
          <span class="t-16 fw-600">最近订单</span>
          <a-space>
            <a-select v-model:value="status" size="small" class="w-120"
              :options="['全部', '待支付', '待出库', '拣货中', '已出库', '已签收', '已取消'].map((v) => ({ value: v, label: v === '全部' ? '全部状态' : v }))" />
            <a-select v-model:value="channel" size="small" class="w-120"
              :options="['全部', '门店POS', '小程序到家', '美团闪购', '饿了么', '京东到家', '社群团购'].map((v) => ({ value: v, label: v === '全部' ? '全部渠道' : v }))" />
            <a-input-search v-model:value="kw" size="small" placeholder="订单号 / 门店名称" class="w-200" />
          </a-space>
        </div>
        <div class="orders-table paged-scroll">
          <a-table size="small" row-key="id" :columns="columns" :data-source="paged.items" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'id'"><ObjRef kind="order" :id="record.id"><span class="num">{{ record.id }}</span></ObjRef></template>
              <template v-else-if="column.key === 'store'"><ObjRef kind="store" :id="record.storeId">{{ storeById(record.storeId)?.name }}</ObjRef></template>
              <template v-else-if="column.key === 'lines'"><span class="num">{{ record.lines.length }}</span></template>
              <template v-else-if="column.key === 'amount'"><span class="num">{{ fmtMoney(record.amount) }}</span></template>
              <template v-else-if="column.key === 'status'"><a-badge :status="statusBadge(record.status)" :text="record.status" /></template>
              <template v-else-if="column.key === 'createdAt'"><span class="num">{{ record.createdAt }}</span></template>
              <template v-else-if="column.key === 'op'">
                <a v-if="record.status === '已出库'" @click="resend(record)">补发</a>
                <span v-else class="c-3">--</span>
              </template>
            </template>
          </a-table>
        </div>
        <AppPagination
          v-model:page="pq.page.value" v-model:size="pq.size.value" class="orders-pager"
          variant="full" :total="paged.total" :default-size="pq.defaultSize" :empty="false"
        />
      </div>

      <div class="side">
        <div class="panel trend">
          <div class="panel-head">
            <HoverCard kind="metric" id="gmv"><span class="t-16 fw-600">近 30 日销售额</span></HoverCard>
            <span class="t-12 c-3">不含税 · 单位：元</span>
          </div>
          <div class="trend-chart"><EChart :option="trendOption" /></div>
        </div>
        <div class="panel rank">
          <div class="panel-head"><span class="t-16 fw-600">门店实付金额排行</span><span class="t-12 c-3">样本订单</span></div>
          <a-table
            size="small" row-key="id" :pagination="false" :data-source="ranking"
            :columns="[
              { title: '#', key: 'idx', width: 40 },
              { title: '门店', key: 'name', ellipsis: true },
              { title: '订单数', key: 'cnt', width: 80, align: 'right' },
              { title: '实付金额（元）', key: 'amount', width: 132, align: 'right' },
            ]"
          >
            <template #bodyCell="{ column, record, index }">
              <span v-if="column.key === 'idx'" class="num c-3">{{ index + 1 }}</span>
              <ObjRef v-else-if="column.key === 'name'" kind="store" :id="record.id">{{ record.name }}</ObjRef>
              <span v-else-if="column.key === 'cnt'" class="num">{{ fmtInt(record.cnt) }}</span>
              <span v-else class="num">{{ fmtMoney(record.amount) }}</span>
            </template>
          </a-table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--gap); }
.kpis { flex: none; flex-direction: row; padding: 16px 0; }
.kpi { flex: 1; padding: 0 24px; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.kpi + .kpi { border-left: var(--border); }
.kpi-val { display: flex; align-items: baseline; gap: 4px; }
.row { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.orders { flex: 1; }
.orders-table { flex: 1; min-height: 0; padding: 0 16px; }
.orders-pager { padding: 4px 16px 8px; }
.side { width: 560px; flex: none; display: flex; flex-direction: column; gap: var(--gap); }
.trend { flex: 1; min-height: 0; }
.trend-chart { flex: 1; min-height: 0; padding: 4px 8px; }
.rank { flex: none; }
.w-120 { width: 120px; }
.w-200 { width: 200px; }
</style>
