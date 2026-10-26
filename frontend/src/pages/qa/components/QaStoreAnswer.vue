<script setup lang="ts">
/**
 * 历史轮次的回答卡（折叠式）：华东 38 家重点门店 9 月销售额。
 * 结果表规则（问数统一）：超过 10 行才分页，size=small、每页 10 行，表头显示“共 N 行”；
 * 合计行固定在表底，按全部数据计算（不是当前页）。翻页写在 URL：?tblPage=2。
 */
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { ApartmentOutlined, DownloadOutlined } from '@ant-design/icons-vue';
import { QA_EC_STORES, fmtMoney, fmtInt, type QaDemoStoreCard, type QaStoreRow } from '@/mock';
import * as qaApi from '@/api/qa';
import { errText } from '../useQaSession';
import { qaLive, qaFallBackIfDown } from '../qaFallback';
import { useQueryPage } from '../useQueryParam';
import AppPagination from '@/components/AppPagination.vue';

/** turnId：真接口时这一轮的 id，结果表走 W10（每页 10 行、合计按全部数据），导出走 W11；没有就用演示数据 */
const props = defineProps<{ card: QaDemoStoreCard; turnId?: string }>();
const PAGE_SIZE = 10;
const page = useQueryPage('tblPage');
const f1 = (n: number) => fmtMoney(n, 1);
/** 演示数据：前端自己分页、合计 */
const demoTotal = QA_EC_STORES.reduce((a, r) => a + r.sales, 0);
const demoLast = QA_EC_STORES.reduce((a, r) => a + r.sales / (1 + r.mom / 100), 0);
const demoSum = { count: QA_EC_STORES.length, sales: demoTotal, mom: (demoTotal / demoLast - 1) * 100, orders: QA_EC_STORES.reduce((a, r) => a + r.orders, 0) };
/** 真接口：当前页 + 后台按全部数据算的合计 */
/** 真接口且后台可用时才走 W10 / W11；后台判定未启动后整卡回退演示数据（同 qaFallback.ts） */
const tid = computed(() => (qaLive.value ? props.turnId : undefined));
const remote = ref<{ items: QaStoreRow[]; total: number; page: number; summary: typeof demoSum } | null>(null);
const loading = ref(false);
let seq = 0;
async function load() {
  if (!tid.value) return;
  const my = ++seq; loading.value = true;
  try {
    const r = await qaApi.getTurnResult<QaStoreRow>(tid.value, { page: page.value, size: PAGE_SIZE });
    if (my === seq) remote.value = { items: r.items, total: r.total, page: r.page, summary: r.summary as unknown as typeof demoSum };
  } catch (e) { if (my === seq && !qaFallBackIfDown(e)) message.error(`结果表加载失败：${errText(e)}`); } finally { if (my === seq) loading.value = false; }
}
watch([tid, page], load, { immediate: true });
const rowCount = computed(() => (tid.value ? remote.value?.total ?? 0 : QA_EC_STORES.length));
const sum = computed(() => (tid.value ? remote.value?.summary ?? { count: 0, sales: 0, mom: 0, orders: 0 } : demoSum));
const paged = computed(() => rowCount.value > PAGE_SIZE);
const pageCount = computed(() => Math.max(1, Math.ceil(rowCount.value / PAGE_SIZE)));
const current = computed(() => (tid.value ? remote.value?.page ?? 1 : Math.min(page.value, pageCount.value)));
const columns = [
  { title: '#', key: 'rank', width: 48 },
  { title: '门店', dataIndex: 'store', key: 'store' },
  { title: '城市', dataIndex: 'city', key: 'city', width: '12%' },
  { title: '销售额（万元）', dataIndex: 'sales', key: 'sales', align: 'right' as const },
  { title: '环比', dataIndex: 'mom', key: 'mom', align: 'right' as const },
  { title: '订单数', dataIndex: 'orders', key: 'orders', align: 'right' as const },
];
const pageRows = computed(() => {
  if (tid.value) return remote.value?.items ?? [];
  return paged.value ? QA_EC_STORES.slice((current.value - 1) * PAGE_SIZE, current.value * PAGE_SIZE) : QA_EC_STORES;
});
const exporting = ref(false);
async function onExport() {
  if (!tid.value) { message.info(`已导出 ${rowCount.value} 行（${props.card.exportFile}）`); return; }
  exporting.value = true;
  try { const name = await qaApi.exportTurn(tid.value, 'xlsx'); message.info(`已导出 ${rowCount.value} 行（${name}）`); } catch (e) { if (qaFallBackIfDown(e)) message.info(`已导出 ${QA_EC_STORES.length} 行（${props.card.exportFile}）`); else message.error(`导出失败：${errText(e)}`); } finally { exporting.value = false; }
}
const pageModel = computed({ get: () => current.value, set: (p: number) => { page.value = p; } });
</script>

<template>
  <div class="qa-sa">
    <div class="qa-sa-head">
      <a-avatar size="small" shape="square" class="qa-sa-av"><template #icon><ApartmentOutlined /></template></a-avatar>
      <span class="fw-600">智能问数</span>
      <span class="c-3">{{ card.metric }} · 依据：{{ card.basis }} · 数据截至 {{ card.dataAsOf }}</span>
    </div>
    <p class="qa-sa-text">{{ card.period }}，{{ card.region }} {{ sum.count }} 家重点门店销售额（含税）合计 <span class="fw-600 num">{{ f1(sum.sales) }} 万元</span>，按销售额从高到低排列。</p>
    <div class="qa-sa-box">
      <div class="qa-sa-box-head">
        <span>按门店拆分</span>
        <a-button size="small" :loading="exporting" @click="onExport"><template #icon><DownloadOutlined /></template>导出</a-button>
      </div>
      <a-table size="small" :columns="columns" :data-source="pageRows" row-key="key" :pagination="false" :loading="loading" class="qa-sa-tbl">
        <template #bodyCell="{ column, record, index }">
          <span v-if="column.key === 'rank'" class="c-3 num">{{ (current - 1) * PAGE_SIZE + index + 1 }}</span>
          <span v-else-if="column.key === 'sales'" class="num">{{ f1(record.sales) }}</span>
          <span v-else-if="column.key === 'mom'" class="num">{{ f1(record.mom) }}%</span>
          <span v-else-if="column.key === 'orders'" class="num">{{ fmtInt(record.orders) }}</span>
        </template>
        <template #summary>
          <a-table-summary fixed>
            <a-table-summary-row class="qa-sa-sum">
              <a-table-summary-cell :index="0" :col-span="3"><span class="fw-600">{{ sum.count }} 家合计（全部数据）</span></a-table-summary-cell>
              <a-table-summary-cell :index="3" align="right"><span class="fw-600 num">{{ f1(sum.sales) }}</span></a-table-summary-cell>
              <a-table-summary-cell :index="4" align="right"><span class="num">{{ f1(sum.mom) }}%</span></a-table-summary-cell>
              <a-table-summary-cell :index="5" align="right"><span class="fw-600 num">{{ fmtInt(sum.orders) }}</span></a-table-summary-cell>
            </a-table-summary-row>
          </a-table-summary>
        </template>
      </a-table>
      <AppPagination v-if="paged" v-model:page="pageModel" variant="simple" :total="rowCount" :size="PAGE_SIZE" unit="行" :empty="false" class="qa-sa-pager" />
    </div>
  </div>
</template>

<style scoped>
.qa-sa { background: var(--g1); border: var(--border); border-radius: var(--radius); padding: 12px 16px; margin: 0 40px 10px 0; }
.qa-sa-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.qa-sa-av { background: var(--g1); border: 1px solid var(--g6); color: var(--brand); }
.qa-sa-text { margin: 0 0 8px; }
.qa-sa-box { border: var(--border); border-radius: var(--radius); padding: 8px 12px; }
.qa-sa-box-head { display: flex; justify-content: space-between; align-items: center; height: 24px; margin-bottom: 4px; }
.qa-sa-tbl :deep(.ant-table-small .ant-table-thead > tr > th),
.qa-sa-tbl :deep(.ant-table-small .ant-table-tbody > tr > td),
.qa-sa-tbl :deep(.ant-table-small .ant-table-summary td) { padding-top: 6px; padding-bottom: 6px; }
.qa-sa-sum :deep(td) { background: var(--g2); }
.qa-sa-pager { margin-top: 8px; }
</style>
