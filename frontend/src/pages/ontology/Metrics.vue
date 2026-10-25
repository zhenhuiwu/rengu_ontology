<script setup lang="ts">
/**
 * 指标口径：左侧指标列表，右侧详情。页签：定义 / 允许的维度 / 版本历史 / 引用与影响。
 * 选中指标写进 ?metric=，页签写进 ?tab=。
 */
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import HoverCard from '@/components/HoverCard.vue';
import EChart from '@/components/EChart.vue';
import AppPagination from '@/components/AppPagination.vue';
import { GRAY, BRAND } from '@/theme';
import { DAILY_GMV, fmtMoney, RULES } from '@/mock';
import DraftBanner from './parts/DraftBanner.vue';
import StartChangeModal from './parts/StartChangeModal.vue';
import { useDraftView } from './parts/useDraftView';
import { message } from 'ant-design-vue';

interface M {
  id: string; name: string; api: string; group: string; unit: string; version: string;
  status: '已发布' | '草稿' | '变更中' | '已下线'; owner: string;
}
const LIST: M[] = [
  { id: 'gmv', name: '销售额（不含税）', api: 'sales_amount_net', group: '销售', unit: '元', version: 'v3', status: '已发布', owner: '陈立军' },
  { id: 'gmv_tax', name: '销售额（含税）', api: 'sales_amount_gross', group: '销售', unit: '元', version: 'v2', status: '已发布', owner: '陈立军' },
  { id: 'repeat_sales', name: '复购客户销售额（不含税）', api: 'repeat_customer_sales_net', group: '销售', unit: '元', version: 'v3', status: '已发布', owner: '刘婷' },
  { id: 'repeat_sales_tax', name: '复购客户销售额（含税）', api: 'repeat_customer_sales_gross', group: '销售', unit: '元', version: 'v1', status: '已发布', owner: '刘婷' },
  { id: 'order_cnt', name: '有效订单数', api: 'valid_order_count', group: '销售', unit: '单', version: 'v1', status: '已发布', owner: '陈立军' },
  { id: 'atv', name: '客单价（不含税）', api: 'avg_ticket_value', group: '销售', unit: '元', version: 'v1', status: '草稿', owner: '刘婷' },
  { id: 'refund_rate', name: '退款率', api: 'refund_rate', group: '销售', unit: '%', version: 'v2', status: '已发布', owner: '刘婷' },
  { id: 'gross_margin', name: '毛利率', api: 'gross_margin_rate', group: '利润', unit: '%', version: 'v2', status: '变更中', owner: '刘婷' },
  { id: 'gross_profit', name: '毛利额', api: 'gross_profit', group: '利润', unit: '元', version: 'v2', status: '已发布', owner: '刘婷' },
  { id: 'ship_ontime', name: '出库及时率', api: 'ship_ontime_rate', group: '履约', unit: '%', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'ship_avg', name: '平均出库时长', api: 'avg_ship_minutes', group: '履约', unit: '分钟', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'stockout', name: '缺货率', api: 'stockout_rate', group: '库存', unit: '%', version: 'v1', status: '已发布', owner: '周晓峰' },
  { id: 'turnover', name: '库存周转天数', api: 'inventory_turnover_days', group: '库存', unit: '天', version: 'v1', status: '已下线', owner: '周晓峰' },
  { id: 'new_member', name: '新增会员数', api: 'new_member_count', group: '会员', unit: '人', version: 'v1', status: '已发布', owner: '吴佳' },
  { id: 'repurchase', name: '复购率', api: 'repurchase_rate', group: '会员', unit: '%', version: 'v2', status: '已发布', owner: '吴佳' },
];
const GROUPS = ['全部', '销售', '利润', '履约', '库存', '会员'];
const statusColor = (s: M['status']) => (s === '已发布' ? 'success' : s === '变更中' ? 'warning' : 'default');

const route = useRoute();
const router = useRouter();
const selected = computed(() => (route.query.metric as string) || 'gmv');
const tab = computed({
  get: () => (route.query.tab as string) || 'def',
  set: (v) => router.replace({ query: { ...route.query, tab: v } }),
});
const pick = (id: string) => router.replace({ query: { ...route.query, metric: id, view: undefined } });
const { draft, isDraft, canEdit, editTip, readonly, reason, setView, start } = useDraftView('metric', () => cur.value.id);
const changeOpen = ref(false);
const changeTarget = ref('');
const openChange = (name: string) => { changeTarget.value = name; changeOpen.value = true; };
const group = ref('全部');
const kw = ref('');
const filtered = computed(() => LIST.filter((m) => (group.value === '全部' || m.group === group.value) && (!kw.value || m.name.includes(kw.value) || m.api.includes(kw.value))));
const LPAGE = 12;
const lpage = ref(1);
watch([group, kw], () => { lpage.value = 1; });
const lpages = computed(() => Math.max(1, Math.ceil(filtered.value.length / LPAGE)));
const list = computed(() => filtered.value.slice((lpage.value - 1) * LPAGE, lpage.value * LPAGE));
const cur = computed(() => LIST.find((m) => m.id === selected.value) ?? LIST[0]);

// 定义（示例只给“销售额（不含税）”写全）
const DEF = [
  { k: '基础对象', v: '订单行（SalesOrderLine）' },
  { k: '时间字段', v: '订单行.出库时间' },
  { k: '聚合方式', v: '求和（可加）' },
  { k: '默认过滤', v: '订单.状态 = 已出库；门店.是否测试店 = 否' },
  { k: '同义词', v: '营业额、销售收入、流水、净销售额' },
  { k: '同名默认', v: '是：用户只说“销售额”时按本指标回答（含税版本不设同名默认）' },
  { k: '负责人', v: '陈立军 · 财务数据组' },
  { k: '生效版本', v: 'v3 · 随本体发布 v2026.10.3 生效' },
  { k: '数据截至', v: '2026-10-24 23:00' },
];
const FORMULA = 'SUM(订单行.不含税金额) − SUM(退款行.不含税退款金额)';

const DIMS = [
  { dim: '时间', hier: '年 › 季 › 月 › 周 › 日', obj: '订单行.出库时间', def: '月', note: '' },
  { dim: '组织', hier: '大区 › 门店', obj: '门店', def: '大区', note: '门店直属考核大区（CR-0005），按出库日的归属计算' },
  { dim: '商品', hier: '大类 › 中类 › 小类 › SKU', obj: '商品', def: '大类', note: '' },
  { dim: '渠道', hier: '渠道', obj: '订单.渠道', def: '', note: '字典 4 项' },
  { dim: '会员', hier: '会员等级 › 会员', obj: '会员', def: '', note: '非会员订单归入“散客”' },
  { dim: '仓库', hier: '仓库', obj: '订单行.发货仓', def: '', note: '' },
];
const DENY = [
  { dim: '供应商', reason: '一个订单行对应多个供应商批次，按供应商拆分会重复计算，请用“采购金额”。' },
  { dim: '促销活动', reason: '一单可参加多个活动，金额不可加，请用“活动让利额”。' },
];

const VERSIONS = [
  { ver: 'v3', release: 'v2026.10.3', status: '生效中', by: '陈立军', at: '2026-10-24 21:17', change: '退款改为按退款行不含税金额扣减；默认过滤增加“非测试门店”' },
  { ver: 'v2', release: 'v2026.08.1', status: '已替代', by: '陈立军', at: '2026-08-05 19:42', change: '时间字段由下单时间改为出库时间，与财务月结一致' },
  { ver: 'v1', release: 'v2026.03.2', status: '已替代', by: '刘婷', at: '2026-03-18 10:06', change: '首次发布' },
];
const REFS = [
  { type: '指标', name: '客单价', note: '分子' },
  { type: '指标', name: '毛利率', note: '分母' },
  { type: '指标', name: '复购客户销售额', note: '基础口径，加会员过滤' },
  ...Object.values(RULES).filter((r) => r.metric === 'gmv').map((r) => ({ type: '规则', name: `${r.id} ${r.name}`, note: `条件：${r.condition}；口径升版后需回放试跑` })),
  { type: '看板', name: '华东大区经营日报', note: '4 个卡片' },
  { type: '看板', name: '门店经营月报', note: '2 个卡片' },
  { type: '认证问答', name: '上月各大区销售额', note: '已认证' },
];

const trend = DAILY_GMV.map((d) => ({ date: d.date, value: Math.round((d.value / 1.13) * 100) / 100 }));
const trendOption = {
  grid: { left: 52, right: 12, top: 12, bottom: 24 },
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
  xAxis: { type: 'category', data: trend.map((d) => d.date), axisLine: { lineStyle: { color: GRAY.g6 } }, axisTick: { show: false }, axisLabel: { color: GRAY.g8, fontSize: 12, interval: 6 } },
  yAxis: { type: 'value', axisLabel: { color: GRAY.g8, fontSize: 12, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` }, splitLine: { lineStyle: { color: GRAY.g4 } } },
  series: [{ type: 'line', data: trend.map((d) => d.value), symbol: 'none', lineStyle: { width: 1.5, color: BRAND } }],
};

const TABLE_PAGER = { pageSize: 10, showSizeChanger: true, pageSizeOptions: ['10', '20', '50'], showQuickJumper: true, showTotal: (t: number) => `共 ${t} 条`, size: 'small' as const };
const dimCols = [
  { title: '维度', dataIndex: 'dim', width: 88 },
  { title: '层级', dataIndex: 'hier' },
  { title: '取自', dataIndex: 'obj', width: 160 },
  { title: '默认粒度', dataIndex: 'def', width: 88 },
  { title: '说明', dataIndex: 'note', ellipsis: { showTitle: true } },
];
const verCols = [
  { title: '口径版本', dataIndex: 'ver', width: 96 },
  { title: '本体发布', dataIndex: 'release', width: 120 },
  { title: '状态', key: 'status', width: 96 },
  { title: '变更说明', dataIndex: 'change' },
  { title: '发布人', dataIndex: 'by', width: 96 },
  { title: '发布时间', dataIndex: 'at', width: 168 },
  { title: '操作', key: 'op', width: 120 },
];
const refCols = [
  { title: '类型', dataIndex: 'type', width: 96 },
  { title: '名称', key: 'name' },
  { title: '引用方式', dataIndex: 'note' },
];
</script>

<template>
  <Teleport defer to="#page-extra">
    <a-button :disabled="readonly" @click="!readonly && openChange('导入指标')">导入</a-button>
    <a-button type="primary" :disabled="readonly" @click="!readonly && openChange('新建指标')">新建指标</a-button>
  </Teleport>

  <div class="mt">
    <div class="panel mt-list">
      <div class="mt-filter">
        <a-input-search v-model:value="kw" placeholder="搜索名称或 API 名" />
        <a-segmented v-model:value="group" :options="GROUPS" block size="small" />
      </div>
      <ul class="mt-items">
        <li v-for="m in list" :key="m.id" :class="{ active: m.id === cur.id }" @click="pick(m.id)">
          <div class="mt-row">
            <span class="t-14 mt-name">{{ m.name }}</span>
            <span class="t-12 num" :class="m.status === '变更中' ? 'c-warning' : 'c-3'">{{ m.status === '已发布' ? '' : m.status + ' · ' }}{{ m.version }}</span>
          </div>
          <div class="mt-row">
            <span class="t-12 c-3 num">{{ m.api }}</span>
            <span class="t-12 c-3">{{ m.group }}</span>
          </div>
        </li>
      </ul>
      <div class="mt-foot">
        <AppPagination v-model:page="lpage" variant="simple" :total="filtered.length" :size="LPAGE" class="mt-pager" empty-text="没有匹配的指标" />
      </div>
    </div>

    <div class="panel mt-detail">
      <DraftBanner v-if="isDraft && draft" :draft="draft"
        @submit="message.success(`${draft.id} 已提交审核，可在“变更与发布”查看`)" @discard="setView('published')" />
      <div class="mt-head">
        <span class="t-16 fw-600 c-1">{{ cur.name }}</span>
        <span class="t-14 c-3 num">{{ cur.api }} · {{ cur.unit }}</span>
        <a-tag :color="statusColor(cur.status)">{{ cur.status }}</a-tag>
        <span class="t-14 c-3">口径 {{ cur.version }}</span>
        <a-segmented v-if="draft && !readonly" size="small" :value="isDraft ? 'draft' : 'published'"
          :options="[{ label: '已发布', value: 'published' }, { label: `草稿 ${draft.id}`, value: 'draft' }]"
          @change="(v: any) => setView(v)" />
        <span class="mt-sp" />
        <a-button>试算</a-button>
        <a-tooltip v-if="readonly" :title="reason"><a-button type="primary" disabled>发起变更</a-button></a-tooltip>
        <a-button v-else-if="!draft" type="primary" @click="openChange(`${cur.name}（${cur.api}）`)">发起变更</a-button>
        <a-button v-else-if="!isDraft" type="primary" @click="setView('draft')">继续编辑草稿</a-button>
      </div>
      <a-tabs v-model:activeKey="tab" class="mt-tabs">
        <a-tab-pane key="def" tab="定义" />
        <a-tab-pane key="dims" tab="允许的维度" />
        <a-tab-pane key="versions" tab="版本历史" />
        <a-tab-pane key="refs" tab="引用与影响" />
      </a-tabs>

      <div v-if="tab === 'def'" class="mt-body mt-def">
        <div class="mt-def-left">
          <div class="mt-sec t-14 fw-600 mt-sec-row">
            <span>计算公式<a-tag v-if="isDraft" color="warning" class="mt-diff">修改</a-tag></span>
            <a-tooltip :title="editTip"><a-button size="small" :disabled="!canEdit">编辑定义</a-button></a-tooltip>
          </div>
          <div class="mt-formula t-14 num">{{ FORMULA }}</div>
          <a-descriptions :column="2" bordered size="small" class="mt-desc">
            <a-descriptions-item v-for="d in DEF" :key="d.k" :label="d.k">{{ d.v }}</a-descriptions-item>
          </a-descriptions>
          <div class="mt-sec t-14 fw-600">允许的维度</div>
          <a-table :columns="dimCols" :data-source="DIMS" :pagination="false" size="small" row-key="dim" />
        </div>
        <div class="mt-def-right">
          <div class="mt-sec t-14 fw-600 mt-sec-row">
            <span>近 30 日试算</span><span class="t-12 c-3 fw-400">按口径 v3 计算 · 数据截至 2026-10-24 23:00</span>
          </div>
          <div class="mt-chart"><EChart :option="trendOption" /></div>
          <div class="mt-sec t-14 fw-600">不允许的维度</div>
          <div v-for="d in DENY" :key="d.dim" class="mt-deny">
            <span class="t-14 c-1">{{ d.dim }}</span>
            <span class="t-12 c-3">{{ d.reason }}</span>
          </div>
          <div class="mt-sec t-14 fw-600">被引用</div>
          <div class="mt-refs t-14">
            <span>指标 <HoverCard kind="metric" id="atv">客单价</HoverCard>、<HoverCard kind="metric" id="gross_margin">毛利率</HoverCard> 等 3 个</span>
            <span>规则 1 条，看板 2 个，认证问答 1 条</span>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'dims'" class="mt-body">
        <a-table :columns="dimCols" :data-source="DIMS" :pagination="false" size="middle" row-key="dim" />
        <div class="mt-sec t-14 fw-600">不允许的维度</div>
        <a-table :columns="[{ title: '维度', dataIndex: 'dim', width: 120 }, { title: '原因', dataIndex: 'reason' }]" :data-source="DENY" :pagination="false" size="middle" row-key="dim" />
      </div>

      <div v-else-if="tab === 'versions'" class="mt-body">
        <a-table :columns="verCols" :data-source="VERSIONS" :pagination="TABLE_PAGER" size="middle" row-key="ver">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'">
              <a-badge :status="record.status === '生效中' ? 'success' : 'default'" :text="record.status" />
            </template>
            <template v-else-if="column.key === 'op'">
              <a-space><a>对比</a><a v-if="record.status !== '生效中'">回滚</a></a-space>
            </template>
          </template>
        </a-table>
        <div class="t-12 c-3 mt-note">口径版本只随本体整版发布生效；回滚会生成一个新的变更单，经评审后随下一次发布生效。</div>
      </div>

      <div v-else class="mt-body">
        <a-table :columns="refCols" :data-source="REFS" :pagination="TABLE_PAGER" size="middle" row-key="name">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'"><a>{{ record.name }}</a></template>
          </template>
        </a-table>
        <div class="t-12 c-3 mt-note">修改本指标时，以上对象会出现在变更单的影响分析里。</div>
      </div>
    </div>
  </div>
  <StartChangeModal v-model:open="changeOpen" :target-name="changeTarget" @ok="start" />
</template>

<style scoped>
.mt { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.mt-list { width: 300px; flex: none; }
.mt-filter { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 8px; flex: none; }
.mt-items { list-style: none; margin: 0; padding: 0 8px; flex: 1; min-height: 0; }
.mt-items li { padding: 6px 8px; border-radius: var(--radius); cursor: pointer; }
.mt-items li:hover { background: var(--g3); }
.mt-items li.active { background: var(--brand-bg); }
.mt-items li.active .mt-name { color: var(--brand); }
.mt-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.mt-pager { flex: 1; min-height: 0; }
.mt-foot { padding: 8px 12px 8px 16px; border-top: var(--border); flex: none; display: flex; align-items: center; justify-content: space-between; }
.mt-detail { flex: 1; }
.mt-diff { margin-inline: 8px 0; font-weight: var(--fw-regular); }
.mt-head { display: flex; align-items: center; gap: 12px; height: 52px; padding: 0 16px; flex: none; }
.mt-sp { flex: 1; }
.mt-tabs { padding: 0 16px; flex: none; }
.mt-tabs :deep(.ant-tabs-nav) { margin-bottom: 0; }
.mt-body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.mt-def { flex-direction: row; gap: 16px; }
.mt-def-left { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.mt-def-right { width: 440px; flex: none; display: flex; flex-direction: column; gap: 8px; padding-left: 16px; border-left: var(--border); }
.mt-sec { margin-top: 4px; }
.mt-sec-row { display: flex; justify-content: space-between; align-items: baseline; }
.mt-formula { padding: 8px 12px; background: var(--g2); border: var(--border); border-radius: var(--radius); font-family: Menlo, Consolas, "Noto Sans Mono CJK SC", monospace; }
.mt-chart { height: 200px; }
.mt-deny { display: flex; flex-direction: column; padding: 6px 0; border-bottom: var(--border); }
.mt-refs { display: flex; flex-direction: column; gap: 4px; }
.mt-note { margin-top: 4px; }
</style>
