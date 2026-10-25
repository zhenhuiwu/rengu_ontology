<script setup lang="ts">
/**
 * 首页（示例页）：演示公共组件的用法——KPI 条（.t-kpi）、分页表格、ObjRef（悬停卡 + 抽屉）、
 * EChart。首页只看不改：订单动作（补发出库单）在订单抽屉里，用 ConfirmActionModal 确认。整页无滚动，表格分页。
 */
import { computed, ref } from 'vue';
import ObjRef from '@/components/ObjRef.vue';
import HoverCard from '@/components/HoverCard.vue';
import EChart from '@/components/EChart.vue';
import AppPagination from '@/components/AppPagination.vue';
import InfoTip from '@/components/InfoTip.vue';
import { usePagedQuery } from '@/composables/usePagedQuery';
import { useRoute, useRouter } from 'vue-router';
import { GRAY } from '@/theme';
import {
  ORDERS, STORES, METRICS, DAILY_GMV_EX, exTax, storeById, paginate, fmtMoney, fmtInt, fmtPct, fmtDelta, type Order,
} from '@/mock';

const route = useRoute();
const router = useRouter();

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
// 与页头“数据截至 2026-10-24 23:00”一致：首页只用截至该时刻的订单（样本订单里有 10 月 25 日的实时单，首页不展示）
const CUTOFF = '2026-10-24 23:00:59';
const CUT_ORDERS = ORDERS.filter((o) => o.createdAt <= CUTOFF);
const rows = computed(() =>
  CUT_ORDERS.filter((o) =>
    (status.value === '全部' || o.status === status.value) &&
    (channel.value === '全部' || o.channel === channel.value) &&
    (!kw.value || o.id.includes(kw.value) || (storeById(o.storeId)?.name ?? '').includes(kw.value)),
  ),
);
const paged = computed(() => paginate(rows.value, { page: pq.page.value, size: pq.size.value }));
const statusBadge = (s: Order['status']) =>
  s === '待支付' ? 'warning' : 'default'; // 状态不用蓝/绿：正常流转都是灰色，待支付需要关注为橙色
const columns = [
  { title: '订单号', dataIndex: 'id', key: 'id', width: 200 },
  { title: '门店', key: 'store', ellipsis: true },
  { title: '渠道', dataIndex: 'channel', key: 'channel', width: 112 },
  { title: '商品数', key: 'lines', width: 76, align: 'right' as const },
  { title: '实付金额（元）', key: 'amount', width: 132, align: 'right' as const },
  { title: '状态', key: 'status', width: 96 },
  { title: '下单时间', dataIndex: 'createdAt', key: 'createdAt', width: 172 },
];

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
const fullRanking = STORES.map((s) => {
  const os = CUT_ORDERS.filter((o) => o.storeId === s.id);
  // 销售额（不含税）= 实付金额 ÷ (1 + 13%)，与指标 gmv 同口径
  return { id: s.id, name: s.name, city: s.city, cnt: os.length, amount: exTax(os.reduce((a, o) => a + o.amount, 0)) };
}).sort((a, b) => b.amount - a.amount).map((r, i) => ({ ...r, rank: i + 1 }));
const ranking = fullRanking.slice(0, 10);
// “全部”：抽屉里看完整排行（?rank=all，可截图复现），simple 分页
const rankOpen = computed(() => route.query.rank === 'all');
const setRankOpen = (v: boolean) => router.push({ query: { ...route.query, rank: v ? 'all' : undefined } });
const RANK_SIZE = 10;
const rankPage = ref(1);
const rankPaged = computed(() => paginate(fullRanking, { page: rankPage.value, size: RANK_SIZE }));
const rankCols = [
  { title: '#', key: 'idx', width: 40 },
  { title: '门店', key: 'name', ellipsis: true },
  { title: '订单数', key: 'cnt', width: 80, align: 'right' as const },
  { title: '销售额（元）', key: 'amount', width: 132, align: 'right' as const },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <span class="t-12 c-3">数据截至 2026-10-24 23:00</span>
    <a-button>导出</a-button>
  </Teleport>

  <div class="home">
    <div class="panel kpis">
      <div v-for="k in kpis" :key="k.m.id" class="kpi">
        <span class="kpi-title"><HoverCard kind="metric" :id="k.m.id"><span class="t-14 c-2">{{ k.m.name }}</span></HoverCard><InfoTip text="统计区间 10 月 1 日—24 日，与上月同期（9 月 1 日—24 日）比较" /></span>
        <div class="kpi-val">
          <span class="t-kpi fw-600 c-1">{{ k.value }}</span>
          <span v-if="k.unit" class="t-14 c-2">{{ k.unit }}</span>
          <span class="t-14 c-2 kpi-delta">较上月同期 <span class="num" :class="k.delta >= 0 ? 'c-2' : 'c-error'">{{ k.deltaText }}</span></span>
        </div>
      </div>
    </div>

    <div class="row">
      <div class="panel orders">
        <div class="panel-head">
          <span class="t-16 fw-600">最近订单（截至 10 月 24 日 23:00）</span>
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
            <span><HoverCard kind="metric" id="gmv"><span class="t-16 fw-600">近 30 日销售额（元）</span></HoverCard><InfoTip text="不含税 · 单位：元" /></span>
          </div>
          <div class="trend-chart"><EChart :option="trendOption" /></div>
        </div>
        <div class="panel rank">
          <div class="panel-head">
            <span><span class="t-16 fw-600">门店销售额排行（不含税）</span><InfoTip text="前 10 家门店 · 10 月 1 日—24 日" /></span>
            <a class="t-14" @click="setRankOpen(true)">全部 {{ fullRanking.length }} 家</a>
          </div>
          <a-table size="small" row-key="id" :pagination="false" :data-source="ranking" :columns="rankCols" class="rank-table">
            <template #bodyCell="{ column, record }">
              <span v-if="column.key === 'idx'" class="num c-3">{{ record.rank }}</span>
              <ObjRef v-else-if="column.key === 'name'" kind="store" :id="record.id">{{ record.name }}</ObjRef>
              <span v-else-if="column.key === 'cnt'" class="num">{{ fmtInt(record.cnt) }}</span>
              <span v-else class="num">{{ fmtMoney(record.amount) }}</span>
            </template>
          </a-table>
        </div>
      </div>
    </div>
  </div>

  <a-drawer :open="rankOpen" :width="640" @close="setRankOpen(false)">
    <template #title>门店销售额排行（不含税）· 全部 {{ fullRanking.length }} 家<InfoTip :text="`销售额（不含税）= 实付金额 ÷ 1.13 · 10 月 1 日—24 日 · 数据截至 2026-10-24 23:00`" /></template>
    <a-table size="small" row-key="id" :pagination="false" :data-source="rankPaged.items" :columns="rankCols">
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'idx'" class="num c-3">{{ record.rank }}</span>
        <ObjRef v-else-if="column.key === 'name'" kind="store" :id="record.id">{{ record.name }}</ObjRef>
        <span v-else-if="column.key === 'cnt'" class="num">{{ fmtInt(record.cnt) }}</span>
        <span v-else class="num">{{ fmtMoney(record.amount) }}</span>
      </template>
    </a-table>
    <AppPagination v-model:page="rankPage" variant="simple" :total="rankPaged.total" :size="RANK_SIZE" class="rank-pager" />
  </a-drawer>
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
.kpi-title { display: inline-flex; align-items: center; }
.kpi-delta { margin-left: 8px; }
.rank-pager { padding-top: 8px; }
.w-120 { width: 120px; }
.w-200 { width: 200px; }
</style>
