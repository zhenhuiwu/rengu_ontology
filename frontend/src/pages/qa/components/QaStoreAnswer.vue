<script setup lang="ts">
/**
 * 历史轮次的回答卡（折叠式）：华东 38 家重点门店 9 月销售额。
 * 结果表规则（问数统一）：超过 10 行才分页，size=small、每页 10 行，表头显示“共 N 行”；
 * 合计行固定在表底，按全部数据计算（不是当前页）。翻页写在 URL：?tblPage=2。
 */
import { computed } from 'vue';
import { ApartmentOutlined, DownloadOutlined } from '@ant-design/icons-vue';
import { QA_EC_STORES, fmtMoney, fmtInt } from '@/mock';
import { useQueryPage } from '../useQueryParam';
import AppPagination from '@/components/AppPagination.vue';

const PAGE_SIZE = 10;
const rows = QA_EC_STORES;
const page = useQueryPage('tblPage');
const paged = rows.length > PAGE_SIZE;
const pageCount = Math.ceil(rows.length / PAGE_SIZE);
const current = computed(() => Math.min(page.value, pageCount));
const f1 = (n: number) => fmtMoney(n, 1);
const total = rows.reduce((a, r) => a + r.sales, 0);
const lastTotal = rows.reduce((a, r) => a + r.sales / (1 + r.mom / 100), 0);
const totalOrders = rows.reduce((a, r) => a + r.orders, 0);
const columns = [
  { title: '#', key: 'rank', width: 48 },
  { title: '门店', dataIndex: 'store', key: 'store' },
  { title: '城市', dataIndex: 'city', key: 'city', width: '12%' },
  { title: '销售额（万元）', dataIndex: 'sales', key: 'sales', align: 'right' as const },
  { title: '环比', dataIndex: 'mom', key: 'mom', align: 'right' as const },
  { title: '订单数', dataIndex: 'orders', key: 'orders', align: 'right' as const },
];
const pageRows = computed(() => (paged ? rows.slice((current.value - 1) * PAGE_SIZE, current.value * PAGE_SIZE) : rows));
const pageModel = computed({ get: () => current.value, set: (p: number) => { page.value = p; } });
</script>

<template>
  <div class="qa-sa">
    <div class="qa-sa-head">
      <a-avatar size="small" shape="square" class="qa-sa-av"><template #icon><ApartmentOutlined /></template></a-avatar>
      <span class="fw-600">智能问数</span>
      <span class="c-3">门店销售额（含税）v2 · 依据：大区 = 华东、口径 = 含税 · 数据截至 2026-10-24 23:00</span>
    </div>
    <p class="qa-sa-text">2026 年 9 月，华东 {{ rows.length }} 家重点门店销售额（含税）合计 <span class="fw-600 num">{{ f1(total) }} 万元</span>，按销售额从高到低排列。</p>
    <div class="qa-sa-box">
      <div class="qa-sa-box-head">
        <span>按门店拆分</span>
        <a-button size="small"><template #icon><DownloadOutlined /></template>导出</a-button>
      </div>
      <a-table size="small" :columns="columns" :data-source="pageRows" row-key="key" :pagination="false" class="qa-sa-tbl">
        <template #bodyCell="{ column, record, index }">
          <span v-if="column.key === 'rank'" class="c-3 num">{{ (current - 1) * PAGE_SIZE + index + 1 }}</span>
          <span v-else-if="column.key === 'sales'" class="num">{{ f1(record.sales) }}</span>
          <span v-else-if="column.key === 'mom'" class="num">{{ f1(record.mom) }}%</span>
          <span v-else-if="column.key === 'orders'" class="num">{{ fmtInt(record.orders) }}</span>
        </template>
        <template #summary>
          <a-table-summary fixed>
            <a-table-summary-row class="qa-sa-sum">
              <a-table-summary-cell :index="0" :col-span="3"><span class="fw-600">{{ rows.length }} 家合计（全部数据）</span></a-table-summary-cell>
              <a-table-summary-cell :index="3" align="right"><span class="fw-600 num">{{ f1(total) }}</span></a-table-summary-cell>
              <a-table-summary-cell :index="4" align="right"><span class="num">{{ f1((total / lastTotal - 1) * 100) }}%</span></a-table-summary-cell>
              <a-table-summary-cell :index="5" align="right"><span class="fw-600 num">{{ fmtInt(totalOrders) }}</span></a-table-summary-cell>
            </a-table-summary-row>
          </a-table-summary>
        </template>
      </a-table>
      <AppPagination v-if="paged" v-model:page="pageModel" variant="simple" :total="rows.length" :size="PAGE_SIZE" unit="行" :empty="false" class="qa-sa-pager" />
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
